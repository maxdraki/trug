import type { Group } from './store.svelte';
import type { Item } from './types';

/**
 * The check-off hold: for the length of the strike (`DUR.check`), a checked row
 * stays on its shelf so its own confirmation can play before it collapses out
 * of the list. The window is that duration and nothing else — the hold exists
 * to let the strike finish, so tuning the strike tunes the hold.
 *
 * Why this exists at all. Tapping a row checks it off, which moves it out of
 * `store.groups` on the same tick — so the ItemRow was DESTROYED in the frame
 * the tap landed, and the two pieces of motion tuned for that moment never ran.
 * Measured across the whole outro: the chip's squash spring stayed at scale(1)
 * and the strike-through's `background-size` stayed at `0% 1px`. The struck row
 * you then saw was a different, freshly-mounted node in the basket drawer.
 *
 * The fix is presentation-only, and deliberately so: the store commits the check
 * immediately (optimistic row moved, op queued, id marked pending) and this
 * simply keeps rendering the row where it stood, wearing its checked state, for
 * as long as the confirmation takes. Nothing here delays a mutation, so nothing
 * here can desync the pending set, double-fire an op, or lose a check-off when
 * the tab closes mid-hold — the worst a dropped hold costs is an animation.
 */

/** A row still rendered on its shelf while its check-off plays. */
export interface HeldRow {
  /** The row as it renders during the hold — status already `checked`. */
  item: Item;
  category: string;
  /** The slot it occupied in that aisle when it was checked off. */
  index: number;
}

export interface HoldSet {
  /** Rows currently held, in the order they were checked off. */
  readonly rows: HeldRow[];
  readonly ids: Set<string>;
  /** Hold `row` for `ms`. A window of 0 (reduced motion) holds nothing. */
  hold(row: HeldRow, ms: number): void;
  release(id: string): void;
  releaseAll(): void;
}

/**
 * Capture the row `id` occupies in the ACTIVE list, ready to be held, or null if
 * it is not there.
 *
 * Null is the answer for the reverse gesture — unchecking from the basket — and
 * that is the whole of the decision not to mirror the hold there. An uncheck's
 * feedback is the row arriving back on its shelf, one aisle-length of list away
 * from the thumb; there is no strike to draw (it is being erased) and no squash
 * (the spring only fires on the way in), so a hold would buy a pause and nothing
 * to look at during it.
 *
 * `groups` must be the list as RENDERED — the output of `withHeldRows`, not the
 * store's own groups. The two differ by exactly the rows that are mid-hold, and
 * a slot counted in a list those rows are missing from is a slot short. Checking
 * two rows off 40ms apart put the second one's index one too low, `withHeldRows`
 * spliced it in above the first, and the aisle visibly swapped them under the
 * thumb — the list jumping mid-check being the one thing the hold was built to
 * stop. Already-held rows are in that list wearing `checked`, and are skipped
 * here: a second tap on one is an uncheck, which holds nothing.
 *
 * `checked_at` is synthesised for the hold's own render — the row wears its
 * checked state for a few frames before the store's copy (with the real
 * timestamp) takes over. Nothing may read it as a true "checked at" time.
 */
export function heldSlot(groups: Group[], id: string): HeldRow | null {
  for (const group of groups) {
    const index = group.items.findIndex((i) => i.id === id);
    if (index < 0) continue;
    const found = group.items[index];
    if (found.status === 'checked') return null;
    return {
      item: { ...found, status: 'checked', checked_at: new Date().toISOString() },
      category: group.category,
      index,
    };
  }
  return null;
}

/**
 * The store's groups with every held row spliced back into the slot it left,
 * re-creating an aisle the store has already emptied.
 *
 * That last part is not a nicety: checking off an aisle's last row destroys the
 * `AisleGroup`, and Svelte skips a local transition whose enclosing block is
 * destroyed — so the held row would vanish in a frame, which is the bug again
 * wearing a different hat.
 *
 * A held id the store is showing again is dropped rather than spliced. An undo,
 * or somebody else unchecking the row over SSE, puts it back on the shelf
 * mid-hold; keying the same id twice in one each-block is a crash, and the
 * store's copy is the true one.
 */
export function withHeldRows(groups: Group[], held: HeldRow[], walkOrder: string[]): Group[] {
  if (held.length === 0) return groups;
  const onShelf = new Set<string>();
  for (const group of groups) for (const it of group.items) onShelf.add(it.id);
  const pending = held.filter((h) => !onShelf.has(h.item.id));
  if (pending.length === 0) return groups;

  const out = groups.map((g) => ({ category: g.category, items: g.items.slice() }));
  for (const row of pending) {
    let group = out.find((g) => g.category === row.category);
    if (!group) {
      group = { category: row.category, items: [] };
      // Re-created where the aisle always sat, so the shelf does not reshuffle
      // under the row for the last frames of its life.
      const rank = walkOrder.indexOf(row.category);
      let at = out.length;
      if (rank >= 0) {
        const after = out.findIndex((g) => {
          const r = walkOrder.indexOf(g.category);
          return r < 0 || r > rank;
        });
        if (after >= 0) at = after;
      }
      out.splice(at, 0, group);
    }
    group.items.splice(Math.min(Math.max(row.index, 0), group.items.length), 0, row.item);
  }
  return out;
}

export function createHoldSet(): HoldSet {
  let rows = $state<HeldRow[]>([]);
  const timers = new Map<string, ReturnType<typeof setTimeout>>();

  function release(id: string): void {
    const timer = timers.get(id);
    if (timer !== undefined) {
      clearTimeout(timer);
      timers.delete(id);
    }
    // One pass, and the length tells us whether it found anything — releasing an
    // id that is not held must not reassign `rows` and wake every reader of it.
    const kept = rows.filter((r) => r.item.id !== id);
    if (kept.length !== rows.length) rows = kept;
  }

  return {
    get rows() {
      return rows;
    },
    get ids() {
      return new Set(rows.map((r) => r.item.id));
    },
    hold(row: HeldRow, ms: number) {
      // Re-holding a row that is already held restarts it rather than stacking a
      // second timer, so a shopper drumming on one row can't leave a stale
      // timeout to yank the next hold out from under itself.
      release(row.item.id);
      if (ms <= 0) return;
      rows = [...rows, row];
      timers.set(
        row.item.id,
        setTimeout(() => release(row.item.id), ms),
      );
    },
    release,
    releaseAll() {
      for (const timer of timers.values()) clearTimeout(timer);
      timers.clear();
      rows = [];
    },
  };
}
