import 'fake-indexeddb/auto';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
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

  it('a check queued against an optimistic id survives the dedup swap', async () => {
    // Silent data loss during a normal offline shop: you add something the
    // client cannot tell is a duplicate (the server's dedup is broader — it
    // folds plurals via the catalogue), check it off in the aisle, and when
    // connectivity returns the add is deduped onto an EXISTING row with a
    // different id. The queued `check` then named an id the server had never
    // heard of, 404'd, and was dropped — the item you physically put in the
    // trolley came back unchecked with no error shown.
    const addItem = vi.fn<() => Promise<{ item: Item; created: boolean }>>(() =>
      Promise.reject(new TypeError('fetch failed')),
    );
    const setStatus = vi.fn<(id: string, status: string) => Promise<Item>>(() =>
      Promise.reject(new TypeError('fetch failed')),
    );
    const api = fakeApi({ addItem, setStatus });
    const queue = freshQueue();
    const store = createStore({ api, queue, walkOrder: ['Other', 'Produce'] });

    // --- offline: add, then check off in the aisle ---
    await store.add('Bananas');
    const optId = flat(store).find((i) => i.name === 'Bananas')!.id;
    await store.toggle(optId);
    expect(store.online).toBe(false);
    expect(store.checked.map((i) => i.id)).toEqual([optId]);
    expect((await queue.pending()).length).toBe(2);

    // --- back online: the add is deduped onto an existing row ---
    const server = item({ id: 'banana-1', name: 'Bananas', category: 'Produce' });
    addItem.mockImplementation(() => Promise.resolve({ item: server, created: false }));
    const statusCalls: [string, string][] = [];
    setStatus.mockImplementation(async (id: string, status: string) => {
      if (id !== server.id) throw new ApiError(404, 'not found');
      statusCalls.push([id, status]);
      return item({ ...server, status: status as any, checked_at: '2026-08-05T00:00:00Z' });
    });
    api.list.mockResolvedValue({
      active: {},
      checked: [item({ ...server, status: 'checked', checked_at: '2026-08-05T00:00:00Z' })],
    });

    await store.retry();

    // Load-bearing: the check actually reached the server, against the id the
    // server handed back — not the dead optimistic one.
    expect(statusCalls).toEqual([['banana-1', 'checked']]);
    expect((await queue.pending()).length).toBe(0);
    expect(store.checked.map((i) => i.id)).toEqual(['banana-1']);
    expect(flat(store)).toHaveLength(0);
  });

  it('rewrites a queued delete onto the deduped id too, and keeps it pending', async () => {
    const addItem = vi.fn<() => Promise<{ item: Item; created: boolean }>>(() =>
      Promise.reject(new TypeError('fetch failed')),
    );
    const remove = vi.fn<(id: string) => Promise<void>>(() =>
      Promise.reject(new TypeError('fetch failed')),
    );
    const api = fakeApi({ addItem, remove });
    const queue = freshQueue();
    const store = createStore({ api, queue, walkOrder: ['Other', 'Produce'] });

    await store.add('Bananas');
    const optId = flat(store).find((i) => i.name === 'Bananas')!.id;
    await store.remove(optId);
    expect((await queue.pending()).length).toBe(2);

    // The add resolves onto an existing row, but the network dies again before
    // the delete goes out: the row must not reappear on the shelf, and its id
    // must stay pending so an SSE echo can't resurrect it either.
    const server = item({ id: 'banana-1', name: 'Bananas', category: 'Produce' });
    addItem.mockImplementation(() => Promise.resolve({ item: server, created: false }));
    await store.retry().catch(() => {});

    expect(flat(store).some((i) => i.name === 'Bananas')).toBe(false);
    expect(store.pendingIds.has('banana-1')).toBe(true);
    expect((await queue.pending()).map((o) => o.itemId)).toEqual(['banana-1']);
  });

  it('a storage failure while repointing is reported as storage, never as offline', async () => {
    // The rewrite is IndexedDB work, not network work. Quota exhaustion, Safari
    // private browsing, an aborted transaction — none of them mean the network
    // is down, so none of them may raise the "you're offline — changes are saved
    // and will sync" banner, which would be false on both counts. The add
    // itself succeeded, so it must still leave the queue.
    const addItem = vi.fn<() => Promise<{ item: Item; created: boolean }>>(() =>
      Promise.reject(new TypeError('fetch failed')),
    );
    const setStatus = vi.fn<(id: string, status: string) => Promise<Item>>(() =>
      Promise.reject(new TypeError('fetch failed')),
    );
    const api = fakeApi({ addItem, setStatus });
    const queue = freshQueue();
    const store = createStore({ api, queue, walkOrder: ['Other', 'Produce'] });

    await store.add('Bananas');
    const optId = flat(store).find((i) => i.name === 'Bananas')!.id;
    await store.toggle(optId);
    expect((await queue.pending()).length).toBe(2);

    const server = item({ id: 'banana-1', name: 'Bananas', category: 'Produce' });
    addItem.mockImplementation(() => Promise.resolve({ item: server, created: false }));
    // The replay must reach this op at all (the queue must not wedge), and the
    // store must not have gone "offline" on the way past the storage failure.
    const onlineDuringReplay: boolean[] = [];
    setStatus.mockImplementation(async (id: string) => {
      onlineDuringReplay.push(store.online);
      if (id !== server.id) throw new ApiError(404, 'not found');
      return item({ ...server, status: 'checked', checked_at: '2026-08-05T00:00:00Z' });
    });
    api.list.mockResolvedValue({ active: { Produce: [server] }, checked: [] });
    const quota = new DOMException('quota exceeded', 'QuotaExceededError');
    vi.spyOn(queue, 'rewriteItemId').mockRejectedValue(quota);
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});

    try {
      await store.retry();

      // The network is fine: no offline banner, no wedged queue.
      expect(onlineDuringReplay).toEqual([true]);
      expect(store.online).toBe(true);
      expect((await queue.pending()).some((o) => o.kind === 'add')).toBe(false);
      expect(errors.mock.calls.some((c) => String(c[0]).startsWith('[trug]'))).toBe(true);
    } finally {
      errors.mockRestore();
    }
  });

  it('re-applies rewritten ops over the deduped row without waiting for a refresh', async () => {
    // Both dedup tests above end in a refresh(), which re-applies the queue from
    // scratch and would mask a broken re-apply inside reconcileAdd. Drive the
    // drain alone: the row must already read as checked and pending.
    const addItem = vi.fn<() => Promise<{ item: Item; created: boolean }>>(() =>
      Promise.reject(new TypeError('fetch failed')),
    );
    const setStatus = vi.fn<(id: string, status: string) => Promise<Item>>(() =>
      Promise.reject(new TypeError('fetch failed')),
    );
    const api = fakeApi({ addItem, setStatus });
    const queue = freshQueue();
    const store = createStore({ api, queue, walkOrder: ['Other', 'Produce'] });

    await store.add('Bananas');
    const optId = flat(store).find((i) => i.name === 'Bananas')!.id;
    await store.toggle(optId);
    expect((await queue.pending()).length).toBe(2);

    // The add lands on an existing server row; the check-off behind it does NOT
    // get through (still offline), and no refresh runs at all.
    const server = item({ id: 'banana-1', name: 'Bananas', category: 'Produce' });
    addItem.mockImplementation(() => Promise.resolve({ item: server, created: false }));
    api.list.mockRejectedValue(new TypeError('fetch failed'));

    await store.retry().catch(() => {}); // drain succeeds for the add, refresh fails

    expect(store.checked.map((i) => i.id)).toEqual(['banana-1']);
    expect(flat(store).some((i) => i.name === 'Bananas')).toBe(false);
    expect(store.pendingIds.has('banana-1')).toBe(true);
    expect((await queue.pending()).map((o) => [o.kind, o.itemId])).toEqual([['check', 'banana-1']]);
  });

  it('reconciles against the row that was marked pending, not the id the op carried', async () => {
    // add() renders a LOCAL match (`existing.id`) and marks that id pending,
    // while the op carries a fresh uuid so the server's reactivate branch runs.
    // If the server folds onto a third row, reconciling against the op's id
    // leaves the rendered row on screen, stuck pending forever.
    const d = deferred<{ item: Item; created: boolean }>();
    const api = fakeApi({
      list: vi.fn(() =>
        Promise.resolve({ active: { Dairy: [item({ id: 'milk-local', name: 'Milk', category: 'Dairy' })] }, checked: [] }),
      ),
      addItem: vi.fn(() => d.promise),
    });
    const queue = freshQueue();
    const store = createStore({ api, queue, walkOrder: ['Other', 'Dairy'] });
    await store.refresh();

    const rewrite = vi.spyOn(queue, 'rewriteItemId');
    const warns = vi.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      const p = store.add('Milk');
      expect(store.pendingIds.has('milk-local')).toBe(true);

      // The server folded onto a third row (its matching is broader than ours).
      d.resolve({ item: item({ id: 'milk-server', name: 'Milk', category: 'Dairy' }), created: false });
      await p;

      expect(flat(store).map((i) => i.id)).toEqual(['milk-server']);
      expect(store.pendingIds.size).toBe(0);
      // The rewrite must repoint the id queued ops actually name.
      expect(rewrite).toHaveBeenCalledWith('milk-local', 'milk-server');
      expect(warns.mock.calls.some((c) => String(c[0]).startsWith('[trug]'))).toBe(true);
    } finally {
      warns.mockRestore();
    }
  });

  it('re-adding a checked item moves that row — no Other detour, no duplicate', async () => {
    // Reported as "the whole list jitters". Re-adding something you have already
    // bought used to fabricate an optimistic row in `Other` under a NEW id while
    // the struck-through row was still in the basket — so the name was on screen
    // twice — and then the server's reply (the original row, reactivated, in its
    // real aisle) destroyed that row and inserted another one somewhere else.
    // Three overlapping height changes for what is logically one move.
    const d = deferred<{ item: Item; created: boolean }>();
    const api = fakeApi({
      list: vi.fn(() =>
        Promise.resolve({
          active: {},
          checked: [item({ id: 'milk-1', name: 'Milk', category: 'Dairy', status: 'checked' })],
        }),
      ),
      addItem: vi.fn(() => d.promise),
    });
    const store = createStore({ api, queue: freshQueue(), walkOrder: ['Other', 'Dairy'] });
    await store.refresh();
    expect(store.checked.map((i) => i.id)).toEqual(['milk-1']);

    const p = store.add('Milk');

    // Synchronously, before the server answers: one row, its own id, its real
    // aisle, and gone from the basket.
    const rows = flat(store).filter((i) => i.name === 'Milk');
    expect(rows).toHaveLength(1);
    expect(rows[0].id).toBe('milk-1');
    expect(rows[0].category).toBe('Dairy');
    expect(store.groups.some((g) => g.category === 'Other')).toBe(false);
    expect(store.checked).toHaveLength(0);

    // The server reactivates the original row and returns it.
    d.resolve({
      item: item({ id: 'milk-1', name: 'Milk', category: 'Dairy', status: 'active' }),
      created: false,
    });
    await p;

    // …and reconciliation is a no-op rather than a second move.
    const after = flat(store).filter((i) => i.name === 'Milk');
    expect(after).toHaveLength(1);
    expect(after[0].id).toBe('milk-1');
    expect(store.groups.some((g) => g.category === 'Other')).toBe(false);
    expect(store.pendingIds.size).toBe(0);
  });

  it('re-adding an item already on the list does not duplicate it either', async () => {
    const d = deferred<{ item: Item; created: boolean }>();
    const api = fakeApi({
      list: vi.fn(() =>
        Promise.resolve({ active: { Dairy: [item({ id: 'milk-1', name: 'Milk', category: 'Dairy' })] }, checked: [] }),
      ),
      addItem: vi.fn(() => d.promise),
    });
    const store = createStore({ api, queue: freshQueue(), walkOrder: ['Other', 'Dairy'] });
    await store.refresh();

    const p = store.add('milk');   // different case — same item to the server
    expect(flat(store).filter((i) => i.name.toLowerCase() === 'milk')).toHaveLength(1);
    expect(store.groups.some((g) => g.category === 'Other')).toBe(false);

    d.resolve({ item: item({ id: 'milk-1', name: 'Milk', category: 'Dairy' }), created: false });
    await p;
    expect(flat(store).filter((i) => i.name.toLowerCase() === 'milk')).toHaveLength(1);
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

  it('onRemoteAdd fires for an item_added frame, with the item', () => {
    const onRemoteAdd = vi.fn();
    const store = createStore({
      api: fakeApi(),
      queue: freshQueue(),
      walkOrder: ['Other'],
      onRemoteAdd,
    });

    const arrived = item({ id: 'r1', name: 'Lemons', source: 'ring' });
    store.applyEvent('item_added', arrived);

    expect(onRemoteAdd).toHaveBeenCalledTimes(1);
    expect(onRemoteAdd).toHaveBeenCalledWith(arrived);
  });

  it('onRemoteAdd does NOT fire for item_updated', () => {
    // That frame also carries note edits and check-offs from other devices, so
    // firing on it would buzz on every check-off during a shop.
    const onRemoteAdd = vi.fn();
    const store = createStore({
      api: fakeApi(),
      queue: freshQueue(),
      walkOrder: ['Other'],
      onRemoteAdd,
    });

    store.applyEvent('item_updated', item({ id: 'r1', name: 'Lemons', source: 'ring' }));

    expect(onRemoteAdd).not.toHaveBeenCalled();
  });

  it('onRemoteAdd does NOT fire for the echo of our own pending add', async () => {
    const d = deferred<{ item: Item; created: boolean }>();
    const api = fakeApi({ addItem: vi.fn(() => d.promise) });
    const onRemoteAdd = vi.fn();
    const store = createStore({ api, queue: freshQueue(), walkOrder: ['Other'], onRemoteAdd });

    const p = store.add('Milk');
    const optId = flat(store).find((i) => i.name === 'Milk')!.id;

    store.applyEvent('item_added', item({ id: optId, name: 'Milk' }));
    expect(onRemoteAdd).not.toHaveBeenCalled();

    d.resolve({ item: item({ id: optId, name: 'Milk' }), created: true });
    await p;
  });

  it('applyEvent still works when onRemoteAdd is omitted', () => {
    const store = createStore({ api: fakeApi(), queue: freshQueue(), walkOrder: ['Other'] });

    expect(() =>
      store.applyEvent('item_added', item({ id: 'r1', name: 'Lemons', source: 'ring' })),
    ).not.toThrow();
    expect(flat(store).some((i) => i.id === 'r1')).toBe(true);
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

describe('a storage failure never silently eats a change', () => {
  // Every mutation applies its optimistic change FIRST and then enqueues the
  // durable op. `enqueue` rejects on any IndexedDB fault — quota, Safari private
  // browsing, a blocked or corrupt database — and nobody was catching it: the
  // row showed on the shelf with a "queued" chip, was never sent, was never
  // durably queued, and vanished at the next refresh. clearChecked was worst:
  // the whole basket disappeared locally with no `clear` op anywhere.
  const quota = () => new DOMException('quota exceeded', 'QuotaExceededError');

  function brokenStorageStore(overrides: Record<string, any> = {}) {
    const api = fakeApi(overrides);
    const queue = freshQueue();
    const errors: string[] = [];
    const store = createStore({
      api,
      queue,
      walkOrder: ['Other', 'Dairy'],
      onError: (m) => errors.push(m),
    });
    return { api, queue, store, errors };
  }

  const silenceConsoleError = () => vi.spyOn(console, 'error').mockImplementation(() => {});
  let consoleError: ReturnType<typeof silenceConsoleError>;
  beforeEach(() => {
    consoleError = silenceConsoleError();
  });
  afterEach(() => consoleError.mockRestore());

  function expectReported(errors: string[]) {
    expect(errors).toHaveLength(1);
    expect(errors[0]).toMatch(/couldn't/i);
    expect(consoleError.mock.calls.some((c) => String(c[0]).startsWith('[trug]'))).toBe(true);
  }

  it('rolls back an add whose op cannot be stored', async () => {
    const { queue, store, errors, api } = brokenStorageStore();
    vi.spyOn(queue, 'enqueue').mockRejectedValue(quota());

    await store.add('Milk');

    expect(flat(store).some((i) => i.name === 'Milk')).toBe(false);
    expect(store.pendingIds.size).toBe(0);
    expect(api.addItem).not.toHaveBeenCalled();
    expectReported(errors);
  });

  it('rolls an add back onto the checked row it reactivated', async () => {
    const { queue, store, errors } = brokenStorageStore({
      list: vi.fn(() =>
        Promise.resolve({
          active: {},
          checked: [item({ id: 'milk-1', name: 'Milk', category: 'Dairy', status: 'checked' })],
        }),
      ),
    });
    await store.refresh();
    vi.spyOn(queue, 'enqueue').mockRejectedValue(quota());

    await store.add('Milk');

    // The row goes back into the basket rather than sitting active-but-unsaved.
    expect(store.checked.map((i) => i.id)).toEqual(['milk-1']);
    expect(flat(store).some((i) => i.name === 'Milk')).toBe(false);
    expect(store.pendingIds.size).toBe(0);
    expectReported(errors);
  });

  it('un-ticks a check whose op cannot be stored', async () => {
    const { queue, store, errors } = brokenStorageStore({
      list: vi.fn(() =>
        Promise.resolve({ active: { Other: [item({ id: 'x', name: 'Bread' })] }, checked: [] }),
      ),
    });
    await store.refresh();
    vi.spyOn(queue, 'enqueue').mockRejectedValue(quota());

    await store.toggle('x');

    expect(store.checked).toHaveLength(0);
    expect(flat(store).map((i) => i.id)).toEqual(['x']);
    expect(store.pendingIds.size).toBe(0);
    expectReported(errors);
  });

  it('puts back a delete whose op cannot be stored', async () => {
    const { queue, store, errors } = brokenStorageStore({
      list: vi.fn(() =>
        Promise.resolve({ active: { Other: [item({ id: 'x', name: 'Bread' })] }, checked: [] }),
      ),
    });
    await store.refresh();
    vi.spyOn(queue, 'enqueue').mockRejectedValue(quota());

    await store.remove('x');

    expect(flat(store).map((i) => i.id)).toEqual(['x']);
    expect(store.pendingIds.size).toBe(0);
    expectReported(errors);
  });

  it('restores the whole basket when a clear cannot be stored', async () => {
    const { queue, store, errors } = brokenStorageStore({
      list: vi.fn(() =>
        Promise.resolve({
          active: {},
          checked: [
            item({ id: 'c1', name: 'Old', status: 'checked' }),
            item({ id: 'c2', name: 'Older', status: 'checked' }),
          ],
        }),
      ),
    });
    await store.refresh();
    vi.spyOn(queue, 'enqueue').mockRejectedValue(quota());

    await store.clearChecked();

    expect(store.checked.map((i) => i.id)).toEqual(['c1', 'c2']);
    expectReported(errors);
  });
});

describe('bookkeeping after a successful request', () => {
  it('is reported as a bug, never as "you are offline"', async () => {
    // exec()'s try used to wrap the API call AND all the post-success local
    // work, so anything thrown by the bookkeeping raised the offline banner
    // immediately after a request that demonstrably succeeded.
    const api = fakeApi({
      // A malformed success payload: the request reached the server and worked,
      // but reconciliation trips over the body.
      addItem: vi.fn(() => Promise.resolve({ item: null as any, created: true })),
    });
    const queue = freshQueue();
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      const store = createStore({ api, queue, walkOrder: ['Other'] });
      await store.add('Milk');

      expect(store.online).toBe(true);
      // The op is done server-side, so it must not sit in the queue for ever.
      expect(await queue.pending()).toEqual([]);
      expect(errors.mock.calls.some((c) => String(c[0]).startsWith('[trug]'))).toBe(true);
    } finally {
      errors.mockRestore();
    }
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
