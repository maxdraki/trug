import { vi } from 'vitest';
import type { Group, Store } from '../store.svelte';
import type { Item } from '../types';

/**
 * Test-only fake of the parts of `Store` the list view reads and writes.
 *
 * It lives in a `.svelte.ts` module for one reason, and it is the whole point of
 * the file: the state has to be `$state`. A plain object behind getters is not
 * reactive, so a view that read it once never re-rendered — and every assertion
 * shaped "the row is STILL on the shelf" passed against a DOM that had simply
 * frozen. Five check-off-hold tests went on passing with the hold feature
 * deleted outright. Reactive state is what makes those assertions mean anything:
 * the row stays because the hold keeps it, not because nothing moved.
 *
 * `toggle` really moves the row, so the view sees the same already-committed
 * state the real optimistic store hands it.
 */
export interface MovingStore {
  store: Store;
  toggle: ReturnType<typeof vi.fn>;
  /** The store's own view of the world, for asserting what was committed. */
  state: { groups: Group[]; checked: Item[]; pending: Set<string> };
}

export function movingStore(groups: Group[], checked: Item[] = []): MovingStore {
  const state = $state({ groups, checked, pending: new Set<string>() });

  const toggle = vi.fn((id: string) => {
    for (const g of state.groups) {
      const i = g.items.findIndex((x) => x.id === id);
      if (i >= 0) {
        const [row] = g.items.splice(i, 1);
        state.groups = state.groups.filter((x) => x.items.length);
        state.checked = [...state.checked, { ...row, status: 'checked' }];
        state.pending = new Set(state.pending).add(id);
        return;
      }
    }
    const back = state.checked.find((x) => x.id === id);
    if (!back) return;
    state.checked = state.checked.filter((x) => x.id !== id);
    state.pending = new Set([...state.pending].filter((p) => p !== id));
    const category = back.category ?? 'Other';
    const group = state.groups.find((g) => g.category === category);
    const row = { ...back, status: 'active' as const };
    if (group) group.items.push(row);
    else state.groups = [...state.groups, { category, items: [row] }];
  });

  /** Really takes the row off the list, like the optimistic store's own
   *  `remove` — so a view that offers an undo is undoing something visible
   *  rather than talking to a DOM that never moved. */
  const remove = vi.fn((id: string) => {
    for (const g of state.groups) {
      const i = g.items.findIndex((x) => x.id === id);
      if (i >= 0) {
        g.items.splice(i, 1);
        state.groups = state.groups.filter((x) => x.items.length);
        return;
      }
    }
    state.checked = state.checked.filter((x) => x.id !== id);
  });

  const clearChecked = vi.fn(() => {
    state.checked = [];
  });

  const view = {
    get groups() {
      return state.groups;
    },
    get checked() {
      return state.checked;
    },
    get pendingIds() {
      return state.pending;
    },
    online: true,
    snapshotAt: null,
    refreshed: true,
    refresh: vi.fn(),
    retry: vi.fn(),
    add: vi.fn(),
    toggle,
    remove,
    reorder: vi.fn(),
    clearChecked,
    applyEvent: vi.fn(),
    dispose: vi.fn(),
  };

  return { store: view as unknown as Store, toggle, state };
}
