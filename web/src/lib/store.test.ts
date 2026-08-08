import 'fake-indexeddb/auto';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createStore, midpointSortKey } from './store.svelte';
import { OpQueue } from './opqueue';
import { ApiError } from './api';
import { readSnapshot, writeSnapshot, clearSnapshot } from './snapshot';
import type { Item, ListResponse } from './types';

// Each store now hydrates from the persisted snapshot on creation; clear it
// before every test so a snapshot left by one case can't seed another's store.
beforeEach(() => clearSnapshot());

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function item(partial: Partial<Item> & { id: string; name: string }): Item {
  return {
    note: null,
    icon: null,
    category: 'Other',
    status: 'active',
    source: null,
    added_by: null,
    created_at: new Date().toISOString(),
    checked_at: null,
    sort_key: 0,
    ...partial,
  };
}

let dbSeq = 0;
function freshQueue() {
  return new OpQueue(`store-test-${dbSeq++}`);
}

function flat(store: ReturnType<typeof createStore>): Item[] {
  return store.groups.flatMap((g) => g.items);
}

function fakeApi(overrides: Partial<Record<string, ReturnType<typeof vi.fn>>> = {}) {
  return {
    list: vi.fn<() => Promise<ListResponse>>(() => Promise.resolve({ active: {}, checked: [] })),
    addItem: vi.fn(),
    setStatus: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    clearChecked: vi.fn(),
    search: vi.fn(),
    top: vi.fn(),
    ...overrides,
  } as any;
}

describe('createStore', () => {
  it('optimistic add appears synchronously before the server resolves', async () => {
    const d = deferred<{ item: Item; created: boolean }>();
    const api = fakeApi({ addItem: vi.fn(() => d.promise) });
    const store = createStore({ api, queue: freshQueue(), walkOrder: ['Other', 'Dairy'] });

    const p = store.add('Milk');

    // Synchronous optimistic row, before any server round-trip.
    const optimistic = flat(store).find((i) => i.name === 'Milk');
    expect(optimistic).toBeDefined();
    expect(optimistic!.category).toBe('Other');
    expect(store.pendingIds.has(optimistic!.id)).toBe(true);

    d.resolve({ item: item({ id: optimistic!.id, name: 'Milk', category: 'Dairy' }), created: true });
    await p;

    const reconciled = flat(store).find((i) => i.name === 'Milk')!;
    expect(reconciled.category).toBe('Dairy');
    expect(store.pendingIds.size).toBe(0);
    expect(store.online).toBe(true);
  });

  it('dedup response (different id) replaces the optimistic row', async () => {
    const d = deferred<{ item: Item; created: boolean }>();
    const api = fakeApi({ addItem: vi.fn(() => d.promise) });
    const store = createStore({ api, queue: freshQueue(), walkOrder: ['Other', 'Dairy'] });

    const p = store.add('Milk');
    const optId = flat(store).find((i) => i.name === 'Milk')!.id;

    // Server matched an existing item with a different id.
    d.resolve({ item: item({ id: 'server-xyz', name: 'Milk', category: 'Dairy' }), created: false });
    await p;

    const rows = flat(store).filter((i) => i.name === 'Milk');
    // The optimistic row is replaced by the server row; dedup by id.
    const ids = new Set(rows.map((r) => r.id));
    expect(ids.has('server-xyz')).toBe(true);
    expect(ids.has(optId)).toBe(false);
    expect(store.pendingIds.has(optId)).toBe(false);
  });

  it('offline add stays pending and survives refresh()', async () => {
    const api = fakeApi({
      addItem: vi.fn(() => Promise.reject(new TypeError('fetch failed'))),
    });
    const queue = freshQueue();
    const store = createStore({ api, queue, walkOrder: ['Other'] });

    await store.add('Eggs');

    const optId = flat(store).find((i) => i.name === 'Eggs')!.id;
    expect(store.online).toBe(false);
    expect(store.pendingIds.has(optId)).toBe(true);
    expect((await queue.pending()).length).toBe(1);

    // Server list comes back (reconnected) but does not yet know about Eggs.
    api.list.mockResolvedValue({ active: {}, checked: [] });
    await store.refresh();

    expect(flat(store).some((i) => i.name === 'Eggs')).toBe(true);
    expect(store.pendingIds.has(optId)).toBe(true);
    expect((await queue.pending()).length).toBe(1);
  });

  it('retry() flushes the queued ops then refreshes', async () => {
    const addItem = vi.fn<() => Promise<{ item: Item; created: boolean }>>(() =>
      Promise.reject(new TypeError('fetch failed')),
    );
    const api = fakeApi({ addItem });
    const queue = freshQueue();
    const store = createStore({ api, queue, walkOrder: ['Other'] });

    // Offline add: op stays queued, store goes offline.
    await store.add('Eggs');
    const optId = flat(store).find((i) => i.name === 'Eggs')!.id;
    expect(store.online).toBe(false);
    expect((await queue.pending()).length).toBe(1);

    // Network returns: the op now succeeds and the list reflects it.
    addItem.mockImplementation(() =>
      Promise.resolve({ item: item({ id: optId, name: 'Eggs' }), created: true }),
    );
    api.list.mockResolvedValue({ active: { Other: [item({ id: optId, name: 'Eggs' })] }, checked: [] });

    await store.retry();

    expect(store.online).toBe(true);
    expect((await queue.pending()).length).toBe(0);
    expect(store.pendingIds.size).toBe(0);
    expect(flat(store).some((i) => i.name === 'Eggs')).toBe(true);
  });

  it("applyEvent('item_removed') removes the item", async () => {
    const api = fakeApi({
      list: vi.fn(() =>
        Promise.resolve({ active: { Other: [item({ id: 'x', name: 'Bread' })] }, checked: [] }),
      ),
    });
    const store = createStore({ api, queue: freshQueue(), walkOrder: ['Other'] });
    await store.refresh();
    expect(flat(store).some((i) => i.name === 'Bread')).toBe(true);

    store.applyEvent('item_removed', { id: 'x' });
    expect(flat(store).some((i) => i.name === 'Bread')).toBe(false);
  });

  it("applyEvent upserts but skips echoes of own pending ops", async () => {
    const d = deferred<{ item: Item; created: boolean }>();
    const api = fakeApi({ addItem: vi.fn(() => d.promise) });
    const store = createStore({ api, queue: freshQueue(), walkOrder: ['Other', 'Dairy'] });

    const p = store.add('Milk');
    const optId = flat(store).find((i) => i.name === 'Milk')!.id;

    // Echo of our own still-pending add must be ignored (would clobber optimistic row).
    store.applyEvent('item_added', item({ id: optId, name: 'Milk', category: 'Dairy' }));
    expect(flat(store).find((i) => i.id === optId)!.category).toBe('Other');

    // Unrelated server item upserts normally.
    store.applyEvent('item_updated', item({ id: 'other', name: 'Beans', category: 'Dairy' }));
    expect(flat(store).some((i) => i.name === 'Beans')).toBe(true);

    d.resolve({ item: item({ id: optId, name: 'Milk', category: 'Dairy' }), created: true });
    await p;
  });

  it('toggle checks an active item and enqueues + reconciles', async () => {
    const api = fakeApi({
      list: vi.fn(() =>
        Promise.resolve({ active: { Other: [item({ id: 'x', name: 'Bread' })] }, checked: [] }),
      ),
      setStatus: vi.fn((id: string, status: string) =>
        Promise.resolve(item({ id, name: 'Bread', status: status as any, checked_at: '2026-08-05T00:00:00Z' })),
      ),
    });
    const store = createStore({ api, queue: freshQueue(), walkOrder: ['Other'] });
    await store.refresh();

    await store.toggle('x');

    expect(flat(store).some((i) => i.id === 'x')).toBe(false);
    expect(store.checked.some((i) => i.id === 'x')).toBe(true);
    expect(store.pendingIds.size).toBe(0);
    expect(api.setStatus).toHaveBeenCalledWith('x', 'checked');
  });

  it('clearChecked empties the checked list', async () => {
    const api = fakeApi({
      list: vi.fn(() =>
        Promise.resolve({
          active: {},
          checked: [item({ id: 'c', name: 'Old', status: 'checked' })],
        }),
      ),
      clearChecked: vi.fn(() => Promise.resolve(1)),
    });
    const store = createStore({ api, queue: freshQueue(), walkOrder: ['Other'] });
    await store.refresh();
    expect(store.checked.length).toBe(1);

    await store.clearChecked();
    expect(store.checked.length).toBe(0);
    expect(api.clearChecked).toHaveBeenCalled();
  });

  it('applyEvent with null category places item in Other group', async () => {
    const api = fakeApi();
    const store = createStore({ api, queue: freshQueue(), walkOrder: ['Dairy', 'Produce'] });

    // Apply an item with category: null via SSE — it groups into 'Other'.
    store.applyEvent(
      'item_added',
      item({ id: 'null-cat', name: 'Mystery', category: null as any }),
    );

    const groups = store.groups;
    const otherGroup = groups.find((g) => g.category === 'Other');
    expect(otherGroup).toBeDefined();
    expect(otherGroup!.items.some((i) => i.id === 'null-cat')).toBe(true);
  });

  it("coalesces null and unknown categories into a single 'Other' when 'Other' is in walk order", async () => {
    const api = fakeApi();
    const store = createStore({ api, queue: freshQueue(), walkOrder: ['Dairy', 'Other'] });

    // A null category, an item literally in 'Other', and an unknown category —
    // all coalesce into the single 'Other' aisle.
    store.applyEvent('item_added', item({ id: 'a', name: 'Mystery', category: null as any }));
    store.applyEvent('item_added', item({ id: 'b', name: 'Batteries', category: 'Other' }));
    store.applyEvent('item_added', item({ id: 'c', name: 'Kryptonite', category: 'Nonsense' }));

    const others = store.groups.filter((g) => g.category === 'Other');
    expect(others.length).toBe(1);
    expect(others[0].items.map((i) => i.id).sort()).toEqual(['a', 'b', 'c']);
  });

  it('places a fresh item directly in its aisle, never in a separate pin group', async () => {
    const api = fakeApi();
    const store = createStore({ api, queue: freshQueue(), walkOrder: ['Dairy', 'Other'] });

    // Fresh item lands straight in its aisle — there is no "Recently added" group.
    store.applyEvent('item_added', item({ id: 'fresh', name: 'Milk', category: 'Dairy' }));
    const groups = store.groups;
    expect(groups.find((g) => g.category === 'Recently added')).toBeUndefined();
    expect(groups.find((g) => g.category === 'Dairy')?.items.some((i) => i.id === 'fresh')).toBe(true);
    // Exactly one appearance across all groups.
    expect(flat(store).filter((i) => i.id === 'fresh').length).toBe(1);

    store.dispose();
  });

  it('groups order their rows by sort_key, not insertion/arrival order', async () => {
    const api = fakeApi({
      list: vi.fn(() =>
        Promise.resolve({
          active: {
            Dairy: [
              item({ id: 'c', name: 'C', category: 'Dairy', sort_key: 30 }),
              item({ id: 'a', name: 'A', category: 'Dairy', sort_key: 10 }),
              item({ id: 'b', name: 'B', category: 'Dairy', sort_key: 20 }),
            ],
          },
          checked: [],
        }),
      ),
    });
    const store = createStore({ api, queue: freshQueue(), walkOrder: ['Dairy', 'Other'] });
    await store.refresh();

    const dairy = store.groups.find((g) => g.category === 'Dairy')!;
    expect(dairy.items.map((i) => i.id)).toEqual(['a', 'b', 'c']);
  });

  it('reorder() optimistically moves a row and persists the sort_key', async () => {
    const update = vi.fn((id: string, fields: any) =>
      Promise.resolve(item({ id, name: id.toUpperCase(), category: 'Dairy', ...fields })),
    );
    const api = fakeApi({
      list: vi.fn(() =>
        Promise.resolve({
          active: {
            Dairy: [
              item({ id: 'a', name: 'A', category: 'Dairy', sort_key: 10 }),
              item({ id: 'b', name: 'B', category: 'Dairy', sort_key: 20 }),
            ],
          },
          checked: [],
        }),
      ),
      update,
    });
    const store = createStore({ api, queue: freshQueue(), walkOrder: ['Dairy', 'Other'] });
    await store.refresh();

    // Drop 'a' below 'b' by giving it a key above b's.
    await store.reorder('a', 25);
    const dairy = store.groups.find((g) => g.category === 'Dairy')!;
    expect(dairy.items.map((i) => i.id)).toEqual(['b', 'a']);
    expect(update).toHaveBeenCalledWith('a', { sort_key: 25 });
  });

  it('reorder() can recategorise, and rolls back on failure', async () => {
    const api = fakeApi({
      list: vi.fn(() =>
        Promise.resolve({
          active: {
            Dairy: [item({ id: 'a', name: 'A', category: 'Dairy', sort_key: 10 })],
            Other: [item({ id: 'z', name: 'Z', category: 'Other', sort_key: 5 })],
          },
          checked: [],
        }),
      ),
      update: vi.fn(() => Promise.reject(new TypeError('fetch failed'))),
    });
    const store = createStore({ api, queue: freshQueue(), walkOrder: ['Dairy', 'Other'] });
    await store.refresh();

    await expect(store.reorder('a', 6, 'Other')).rejects.toBeInstanceOf(TypeError);
    // Rolled back: 'a' is still in Dairy, not Other.
    expect(store.groups.find((g) => g.category === 'Dairy')?.items.some((i) => i.id === 'a')).toBe(true);
    expect(store.groups.find((g) => g.category === 'Other')?.items.some((i) => i.id === 'a')).toBe(false);
    expect(store.online).toBe(false);
  });
});

describe('snapshot hydration + persistence', () => {
  it('hydrates groups (and checked) from the snapshot before any fetch resolves', () => {
    writeSnapshot(
      [
        item({ id: 'a', name: 'Milk', category: 'Dairy' }),
        item({ id: 'b', name: 'Old', status: 'checked' }),
      ],
      1_700_000_000_000,
    );
    // list() is never awaited here — the shelf must already read the snapshot.
    const store = createStore({ api: fakeApi(), queue: freshQueue(), walkOrder: ['Dairy', 'Other'] });

    expect(store.groups.find((g) => g.category === 'Dairy')?.items.some((i) => i.name === 'Milk')).toBe(true);
    expect(store.checked.some((i) => i.id === 'b')).toBe(true);
    expect(store.snapshotAt).toBe(1_700_000_000_000);
    expect(store.refreshed).toBe(false);
  });

  it('re-applies queued offline ops on top of the hydrated snapshot at boot', async () => {
    writeSnapshot([item({ id: 'a', name: 'Milk' })], Date.now());
    const queue = freshQueue();
    // A queued offline check-off of the snapshot row, present before boot.
    await queue.enqueue({ opId: 'op1', kind: 'check', itemId: 'a', ts: new Date().toISOString() });

    const store = createStore({ api: fakeApi(), queue, walkOrder: ['Other'] });

    // Boot re-applies the queue asynchronously (IndexedDB) — not only on refresh.
    await vi.waitFor(() => expect(store.pendingIds.has('a')).toBe(true));
    expect(store.checked.some((i) => i.id === 'a')).toBe(true);
  });

  it('debounces persistence and coalesces a burst into one write', () => {
    vi.useFakeTimers();
    try {
      const store = createStore({ api: fakeApi(), queue: freshQueue(), walkOrder: ['Other'] });
      store.applyEvent('item_added', item({ id: 'a', name: 'A' }));
      store.applyEvent('item_added', item({ id: 'b', name: 'B' }));
      // Nothing written yet — still inside the 500ms debounce window.
      expect(readSnapshot()).toBeNull();

      vi.advanceTimersByTime(500);
      const snap = readSnapshot();
      expect(snap!.items.map((i) => i.id).sort()).toEqual(['a', 'b']);
    } finally {
      vi.useRealTimers();
    }
  });

  it('refresh() marks refreshed and persists the reconciled list immediately', async () => {
    const api = fakeApi({
      list: vi.fn(() =>
        Promise.resolve({ active: { Other: [item({ id: 'x', name: 'Bread' })] }, checked: [] }),
      ),
    });
    const store = createStore({ api, queue: freshQueue(), walkOrder: ['Other'] });
    await store.refresh();

    expect(store.refreshed).toBe(true);
    expect(readSnapshot()!.items.some((i) => i.name === 'Bread')).toBe(true);
  });
});

describe('midpointSortKey', () => {
  it('returns the midpoint between two neighbours', () => {
    expect(midpointSortKey(10, 20)).toBe(15);
  });
  it('drops below the first row (next only)', () => {
    expect(midpointSortKey(undefined, 10)).toBe(9);
  });
  it('drops above the last row (prev only)', () => {
    expect(midpointSortKey(10, undefined)).toBe(11);
  });
  it('falls back to epoch seconds for an empty target', () => {
    const before = Date.now() / 1000;
    const k = midpointSortKey(undefined, undefined);
    expect(k).toBeGreaterThanOrEqual(before);
  });
});
