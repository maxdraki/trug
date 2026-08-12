import { describe, it, expect, vi, afterEach } from 'vitest';
import { createHoldSet, heldSlot, withHeldRows, type HeldRow } from './hold.svelte';
import type { Group } from './store.svelte';
import type { Item } from './types';

function item(partial: Partial<Item> & { id: string; name: string }): Item {
  return {
    note: null,
    icon: null,
    category: 'Drinks',
    status: 'active',
    source: null,
    added_by: null,
    created_at: '2026-08-10T09:00:00.000Z',
    checked_at: null,
    sort_key: 0,
    ...partial,
  };
}

const WALK = ['Produce', 'Drinks', 'Cupboard'];

const beer = item({ id: 'beer', name: 'Beer' });
const soda = item({ id: 'soda', name: 'Soda' });
const rice = item({ id: 'rice', name: 'Rice', category: 'Cupboard' });

function groups(): Group[] {
  return [
    { category: 'Drinks', items: [beer, soda] },
    { category: 'Cupboard', items: [rice] },
  ];
}

afterEach(() => {
  vi.useRealTimers();
});

describe('heldSlot', () => {
  it('captures an active row with its aisle and slot, already checked', () => {
    const slot = heldSlot(groups(), 'soda');
    expect(slot).not.toBeNull();
    expect(slot!.category).toBe('Drinks');
    expect(slot!.index).toBe(1);
    expect(slot!.item.status).toBe('checked');
    expect(slot!.item.checked_at).not.toBeNull();
  });

  it('returns null for a row that is already held', () => {
    // The rendered list carries held rows wearing `checked`. A second tap on one
    // is an uncheck, which holds nothing — and must not capture a fresh slot.
    const rendered: Group[] = [
      { category: 'Drinks', items: [{ ...beer, status: 'checked' }, soda] },
    ];
    expect(heldSlot(rendered, 'beer')).toBeNull();
  });

  it('returns null for a row that is not in the active list', () => {
    // The reverse gesture — unchecking in the basket — never holds: the row is
    // not on a shelf to be held on.
    expect(heldSlot(groups(), 'nope')).toBeNull();
  });
});

describe('withHeldRows', () => {
  it('returns the store groups untouched when nothing is held', () => {
    const g = groups();
    expect(withHeldRows(g, [], WALK)).toBe(g);
  });

  it('puts a held row back in the slot it was checked off from', () => {
    const slot = heldSlot(groups(), 'beer')!;
    const store: Group[] = [{ category: 'Drinks', items: [soda] }];
    const merged = withHeldRows(store, [slot], WALK);
    expect(merged[0].items.map((i) => i.id)).toEqual(['beer', 'soda']);
    expect(merged[0].items[0].status).toBe('checked');
    // The store's own arrays are never mutated.
    expect(store[0].items.map((i) => i.id)).toEqual(['soda']);
  });

  it('re-creates an aisle the store has already emptied, in walk order', () => {
    // Checking off the last row of an aisle destroys the aisle, and with it any
    // outro its rows might have played. The held row keeps the shelf alive.
    const slot = heldSlot(groups(), 'rice')!;
    const store: Group[] = [{ category: 'Drinks', items: [beer, soda] }];
    const merged = withHeldRows(store, [slot], WALK);
    expect(merged.map((g) => g.category)).toEqual(['Drinks', 'Cupboard']);
    expect(merged[1].items.map((i) => i.id)).toEqual(['rice']);
  });

  it('drops a held row the store is showing again', () => {
    // An undo, or somebody else unchecking it over SSE, puts the row back on the
    // shelf mid-hold. Splicing the held copy in as well would key the same id
    // twice in one each-block.
    const slot = heldSlot(groups(), 'beer')!;
    expect(withHeldRows(groups(), [slot], WALK)).toEqual(groups());
  });

  it('holds two rows from the same aisle without either losing its slot', () => {
    const first = heldSlot(groups(), 'beer')!;
    const second = heldSlot(groups(), 'soda')!;
    const store: Group[] = [{ category: 'Drinks', items: [] }];
    const merged = withHeldRows(store, [first, second], WALK);
    expect(merged[0].items.map((i) => i.id)).toEqual(['beer', 'soda']);
  });

  it('keeps the aisle in order through two rapid check-offs', () => {
    // The regression: tapping both rows 40ms apart swapped them. The second
    // slot has to be read off the list as RENDERED (holds spliced back in),
    // not off the store's, which the first row has already left.
    const held: HeldRow[] = [];
    let store: Group[] = [{ category: 'Drinks', items: [beer, soda] }];
    const rendered = () => withHeldRows(store, held, WALK);

    held.push(heldSlot(rendered(), 'beer')!);
    store = [{ category: 'Drinks', items: [soda] }];
    held.push(heldSlot(rendered(), 'soda')!);
    store = [{ category: 'Drinks', items: [] }];

    expect(rendered()[0].items.map((i) => i.id)).toEqual(['beer', 'soda']);
  });
});

describe('createHoldSet', () => {
  it('holds a row for the given window, then lets it go', () => {
    vi.useFakeTimers();
    const holds = createHoldSet();
    holds.hold(heldSlot(groups(), 'beer')!, 150);

    expect(holds.ids.has('beer')).toBe(true);
    vi.advanceTimersByTime(149);
    expect(holds.ids.has('beer')).toBe(true);
    vi.advanceTimersByTime(1);
    expect(holds.rows).toEqual([]);
  });

  it('does not hold at all for a zero window', () => {
    // Reduced motion routes DUR.check through d(), which is 0 — the row must go
    // immediately, with no delay to sit through.
    const holds = createHoldSet();
    holds.hold(heldSlot(groups(), 'beer')!, 0);
    expect(holds.rows).toEqual([]);
  });

  it('keeps rapid check-offs independent', () => {
    vi.useFakeTimers();
    const holds = createHoldSet();
    holds.hold(heldSlot(groups(), 'beer')!, 150);
    vi.advanceTimersByTime(100);
    holds.hold(heldSlot(groups(), 'rice')!, 150);

    expect([...holds.ids]).toEqual(['beer', 'rice']);
    vi.advanceTimersByTime(50);
    expect([...holds.ids]).toEqual(['rice']);
    vi.advanceTimersByTime(100);
    expect(holds.rows).toEqual([]);
  });

  it('releases on demand and cancels the timer that would have', () => {
    vi.useFakeTimers();
    const holds = createHoldSet();
    const slot = heldSlot(groups(), 'beer')!;
    holds.hold(slot, 150);
    holds.release('beer');
    expect(holds.rows).toEqual([]);

    // Re-held inside the original window: the cancelled timer must not fire and
    // cut the second hold short.
    holds.hold(slot, 150);
    vi.advanceTimersByTime(149);
    expect(holds.ids.has('beer')).toBe(true);
  });

  it('never stacks two timers on one row', () => {
    vi.useFakeTimers();
    const holds = createHoldSet();
    const slot = heldSlot(groups(), 'beer')!;
    holds.hold(slot, 150);
    holds.hold(slot, 150);
    expect(holds.rows).toHaveLength(1);
    vi.advanceTimersByTime(150);
    expect(holds.rows).toEqual([]);
  });

  it('lets everything go at once on teardown', () => {
    vi.useFakeTimers();
    const holds = createHoldSet();
    holds.hold(heldSlot(groups(), 'beer')!, 150);
    holds.hold(heldSlot(groups(), 'rice')!, 150);
    holds.releaseAll();
    expect(holds.rows).toEqual([]);
    // And no timer survives to touch state after the view is gone.
    expect(vi.getTimerCount()).toBe(0);
  });
});
