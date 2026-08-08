import { tick } from 'svelte';
import { d, DUR, reducedMotion } from './motion';
import { swallowNextClick } from './dom';
import { midpointSortKey } from './store.svelte';
import type { Group } from './store.svelte';
import type { Item } from './types';

/**
 * Hand-rolled pointer-events drag-to-reorder.
 *
 * Chosen over svelte-dnd-action: the codebase already leans on `animate:flip`
 * for row motion, and a bespoke controller lets the dragged row settle through
 * that same FLIP path with no second animation engine to reconcile against
 * Svelte 5 runes. It also gives exact control over the two gestures this needs
 * — long-press-to-lift on touch, move-threshold lift on mouse/pen — plus the
 * cross-aisle recategorise drop, which the library would not model directly.
 *
 * Performance model (the reason this is buttery, not jittery):
 *
 *  - The lifted row follows the pointer via a *direct DOM transform* on the
 *    captured node, coalesced to one style write per animation frame. No
 *    reactive state is touched per pointer move, so the follow never waits on a
 *    Svelte flush.
 *  - Row geometry (aisle bounds + each row's resting midline) is measured ONCE
 *    at lift and re-measured only on scroll. Hit-testing reads those cached
 *    numbers — zero getBoundingClientRect / elementFromPoint inside the move
 *    loop. Crucially, we test the pointer against *resting* midlines, not the
 *    live (gap-shifted) positions, so opening the gap can't feed back into the
 *    hit-test and make it flap.
 *  - The prospective drop index carries hysteresis: it only flips once the
 *    pointer crosses a row's midline plus a small dead zone, so a jittery
 *    pointer sitting on a boundary can't oscillate the gap.
 *
 * Only when the *integer* drop slot (or its aisle) actually changes do we write
 * reactive state — that write is what parts the list, and it fires a handful of
 * times per drag rather than once per pointer sample. On release the slot is
 * turned into a `sort_key` (midpoint of its neighbours) and persisted through
 * `deps.reorder`; the list then re-sorts and FLIP settles it.
 */

export interface DragDeps {
  getGroups: () => Group[];
  reorder: (id: string, sortKey: number, category?: string) => Promise<void>;
}

/** The prospective (or committed) drop slot: an aisle plus an index into that
 * aisle's item list *with the dragged row removed*. */
export interface DropTarget {
  category: string;
  index: number;
}

export interface DragController {
  /** Id of the row currently lifted, or null. Drives the source row's styling. */
  readonly draggingId: string | null;
  /** Category whose shelf label should highlight as a recategorise target. */
  readonly dropCategory: string | null;
  /** Live prospective drop slot. What is shown as the open gap is exactly what a
   * release commits. Updated only when the integer slot changes. */
  readonly target: DropTarget | null;
  /** Aisle the dragged row started in — the source group parts to close up. */
  readonly originCategory: string | null;
  /** Id of the row currently playing its post-release settle, or null. While set,
   * the keyed list must NOT also run its own FLIP / crossfade flight on this row —
   * the controller hand-rolls the settle (same-aisle) or flies a ghost over it
   * (cross-aisle) from the release position so the two never double up. */
  readonly settlingId: string | null;
  /** Id of the row whose real DOM node is held invisible while a cross-aisle
   * ghost flies to its slot. The row is revealed (opacity restored) the instant
   * the ghost lands, so the eye only ever tracks the one travelling clone. */
  readonly ghostId: string | null;
  /** Height (px) of the lifted row: the size of the gap the list opens for it. */
  readonly gapHeight: number;
  /** Wire to a handle's `onpointerdown`; `rowEl` is the element to lift. */
  start(item: Item, category: string, ev: PointerEvent, rowEl: HTMLElement): void;
}

/**
 * Geometric drop-index with hysteresis — the source of truth shared by the live
 * gap preview and (via the cached midlines at release) the commit.
 *
 * `mids` is the ascending list of resting vertical midlines of the aisle's rows
 * with the dragged row removed. The result is the number of those rows the
 * pointer `y` sits below — i.e. an index into that same list, `mids.length`
 * meaning append.
 *
 * A boundary (row midline) only flips once the pointer is `deadZone` px past it,
 * and which way the dead band leans is decided by `committed` (the previously
 * accepted index for this aisle): rows already *above* the committed slot stay
 * crossed until the pointer retreats `deadZone` back above their midline; rows
 * *at or below* it only count as crossed once the pointer advances `deadZone`
 * past their midline. Pass `committed = -1` for a fresh aisle (no stickiness —
 * plain midline test). This makes the index stable to sub-`deadZone` jitter
 * sitting on a boundary while still flipping cleanly once the pointer commits.
 */
export function targetIndexHysteretic(
  mids: number[],
  y: number,
  committed: number,
  deadZone: number,
): number {
  let index = 0;
  for (let i = 0; i < mids.length; i++) {
    const threshold = i < committed ? mids[i] - deadZone : mids[i] + deadZone;
    if (y > threshold) index = i + 1;
  }
  return index;
}

/**
 * Vertical shift (px) to apply to a non-dragged row so the list parts around
 * the prospective drop — opening a one-row gap where the lifted item will land.
 *
 * Same-aisle drags relocate the dragged row's own hole: rows physically between
 * the source slot and the target slot slide by `gap` to move the opening. A
 * cross-aisle drag closes up the source aisle (rows below the lift rise) and
 * opens a gap in the target aisle (rows at/after the drop index descend).
 *
 * `rowIndex` is the row's position within its rendered aisle (which still
 * includes the dragged row in the source aisle). Callers must not pass the
 * dragged row itself — its transform is driven by the pointer, not this.
 */
export function rowGapShift(
  rowCategory: string,
  rowIndex: number,
  drag: { originCategory: string; originIndex: number; targetCategory: string; targetIndex: number },
  gap: number,
): number {
  const { originCategory, originIndex, targetCategory, targetIndex: ti } = drag;

  if (originCategory === targetCategory) {
    // Same aisle: move the dragged row's hole from originIndex to the gap at ti.
    if (rowCategory !== originCategory) return 0;
    const filtered = rowIndex > originIndex ? rowIndex - 1 : rowIndex; // index with dragged removed
    const targetPhysical = filtered < ti ? filtered : filtered + 1; // gap sits at ti
    return (targetPhysical - rowIndex) * gap;
  }
  // Cross aisle: source closes below the lift; target opens at the drop index.
  if (rowCategory === originCategory) return rowIndex > originIndex ? -gap : 0;
  if (rowCategory === targetCategory) return rowIndex >= ti ? gap : 0;
  return 0;
}

/**
 * Vertical space (px) an aisle reserves during a *cross-aisle* drag so the
 * per-row gap shift never overflows its aisle into the next.
 *
 * The per-row `rowGapShift` only rearranges rows *within* an aisle — it does not
 * change the aisle's box height. On its own, opening a gap in a short target
 * aisle slides its last row down past the aisle's final hairline and over the
 * next shelf header. So the aisle box itself must resize: the TARGET grows by
 * one row-height (`+gap`) to hold the opening gap, and the ORIGIN shrinks by one
 * row-height (`-gap`) to absorb the lifted row's now-vacated slot. Being equal
 * and opposite, the two resizes cancel for every aisle between/after them, so no
 * bystander aisle moves — only the origin's and target's own rows shift.
 *
 * Same-aisle drags relocate the hole within one aisle (net-zero height), so they
 * reserve nothing.
 */
export function aisleReserve(
  aisleCategory: string,
  drag: { originCategory: string; targetCategory: string },
  gap: number,
): number {
  if (drag.originCategory === drag.targetCategory) return 0;
  if (aisleCategory === drag.targetCategory) return gap;
  if (aisleCategory === drag.originCategory) return -gap;
  return 0;
}

const LONGPRESS_MS = 300; // touch: hold this long to lift
const MOVE_THRESHOLD = 6; // mouse/pen: move this far to lift (vs. a click)
const SCROLL_CANCEL = 12; // touch: pre-lift movement beyond this = a scroll
const DEAD_ZONE = 8; // px past a row midline before the gap flips (hysteresis)

/** Resting geometry of one aisle, captured at lift. `mids` excludes the dragged
 * row (it has no slot of its own to drop into). */
interface AisleGeom {
  category: string;
  top: number;
  bottom: number;
  mids: number[];
}

export function createDragController(deps: DragDeps): DragController {
  let draggingId = $state<string | null>(null);
  let dropCategory = $state<string | null>(null);
  // Reactive so the list can part around the live drop slot — but written ONLY
  // when the integer slot (or aisle) changes, never per pointer sample.
  let target = $state<DropTarget | null>(null);
  let originCategoryReactive = $state<string | null>(null);
  let gapHeight = $state(0);
  // Id of the row mid-settle. Drives the keyed list to skip its FLIP / crossfade
  // for that one row so the hand-rolled release→slot settle is the only motion.
  let settlingId = $state<string | null>(null);
  // Id of the row hidden behind a flying cross-aisle ghost (see ghostSettle).
  let ghostId = $state<string | null>(null);

  // Per-session, non-reactive state.
  let item: Item | null = null;
  let originCategory = '';
  let rowEl: HTMLElement | null = null;
  let handleEl: HTMLElement | null = null;
  let pointerId = -1;
  let startX = 0;
  let startY = 0;
  let armed = false; // pointer down, not yet lifted
  let lifted = false;
  let longPress: ReturnType<typeof setTimeout> | undefined;

  // Cached geometry + the coalescing frame loop.
  let geom: AisleGeom[] = [];
  let rafId = 0;
  let latestX = 0;
  let latestY = 0;
  // Last accepted slot, read back as the hysteresis anchor for the next frame.
  let committedCategory = '';
  let committedIndex = -1;

  function applyTransform(dx: number, dy: number): void {
    if (!rowEl) return;
    const scale = reducedMotion() ? 1 : 1.03;
    rowEl.style.transform = `translate(${dx}px, ${dy}px) scale(${scale})`;
  }

  /** Snapshot every aisle's bounds and its rows' resting midlines. The dragged
   * row is filtered out of `mids` — it owns no drop slot. Cheap-ish, but only
   * ever called on lift and on scroll, never inside the pointer loop. */
  function measure(): void {
    if (!item) return;
    const dragged = item.id;
    const aisles = document.querySelectorAll<HTMLElement>('[data-drag-aisle]');
    geom = Array.from(aisles).map((a) => {
      const r = a.getBoundingClientRect();
      const mids: number[] = [];
      a.querySelectorAll<HTMLElement>('[data-drag-id]').forEach((rw) => {
        if (rw.getAttribute('data-drag-id') === dragged) return;
        const rr = rw.getBoundingClientRect();
        mids.push(rr.top + rr.height / 2);
      });
      return {
        category: a.getAttribute('data-drag-aisle') ?? '',
        top: r.top,
        bottom: r.bottom,
        mids,
      };
    });
  }

  /** Resolve the prospective drop slot from cached geometry alone. */
  function computeTarget(y: number): DropTarget | null {
    if (!item || geom.length === 0) return null;
    let aisle = geom.find((a) => y >= a.top && y <= a.bottom);
    // Off the ends of the list: clamp to the nearest aisle so a gap is always
    // shown (feels smoother than the gap vanishing at the edges).
    if (!aisle) aisle = y < geom[0].top ? geom[0] : geom[geom.length - 1];
    const committed = aisle.category === committedCategory ? committedIndex : -1;
    const index = targetIndexHysteretic(aisle.mids, y, committed, DEAD_ZONE);
    return { category: aisle.category, index };
  }

  function lift(): void {
    if (!rowEl || !item) return;
    armed = false;
    lifted = true;
    // Measure the resting layout BEFORE the gap opens (target still null).
    measure();
    window.addEventListener('scroll', onScroll, { capture: true, passive: true });
    draggingId = item.id;
    originCategoryReactive = originCategory;
    // The gap the list opens is the height of the row being lifted.
    gapHeight = rowEl.offsetHeight;
    rowEl.classList.add('lifted');
    rowEl.style.willChange = 'transform';
    rowEl.style.pointerEvents = 'none'; // hit-testing is cached anyway; keeps clicks off
    applyTransform(0, 0);
  }

  /** One style write + one hit-test per animation frame; stale frames dropped. */
  function frame(): void {
    rafId = 0;
    if (!lifted) return;
    applyTransform(latestX - startX, latestY - startY);
    const raw = computeTarget(latestY);
    const changed =
      (raw === null) !== (target === null) ||
      (raw !== null && target !== null && (raw.category !== target.category || raw.index !== target.index));
    if (!changed) return;
    target = raw;
    committedCategory = raw?.category ?? '';
    committedIndex = raw?.index ?? -1;
    dropCategory = raw && raw.category !== originCategory ? raw.category : null;
    // Instrumentation hook: increments only on a real slot flip, so a test can
    // assert the displaced-row set changed a handful of times, not per sample.
    if (typeof window !== 'undefined') {
      const w = window as unknown as { __dragTargetChanges?: number };
      w.__dragTargetChanges = (w.__dragTargetChanges ?? 0) + 1;
    }
  }

  function onScroll(): void {
    if (lifted) measure();
  }

  function onMove(ev: PointerEvent): void {
    if (ev.pointerId !== pointerId) return;
    if (armed && !lifted) {
      const dist = Math.hypot(ev.clientX - startX, ev.clientY - startY);
      if (ev.pointerType === 'touch') {
        // Moving before the long-press fires means the user is scrolling.
        if (dist > SCROLL_CANCEL) cleanup();
        return;
      }
      if (dist > MOVE_THRESHOLD) lift();
      if (!lifted) return;
    }
    if (!lifted) return;
    ev.preventDefault(); // stop page scroll fighting the drag
    latestX = ev.clientX;
    latestY = ev.clientY;
    if (!rafId) rafId = requestAnimationFrame(frame);
  }

  function onUp(ev: PointerEvent): void {
    if (ev.pointerId !== pointerId) return;
    if (lifted && item) {
      // Flush any pending frame's geometry so the commit matches the shown gap.
      const t = target ?? computeTarget(ev.clientY);
      const el = rowEl;
      const id = item.id;
      // Correct FLIP semantics: First = the row's on-screen rect AT RELEASE, with
      // the lifted follow-transform still applied (this is where the pointer holds
      // it). Captured BEFORE anything is cleared or the reorder committed.
      const first = el?.getBoundingClientRect() ?? null;
      // A same-aisle (or no-op) drop keeps the SAME keyed DOM node — Svelte just
      // re-slots it, so we can hand-roll the settle on that very element. A
      // cross-aisle drop destroys the row in its source aisle and re-creates it in
      // the target, which the out/in flight already animates; leave that path be.
      const sameAisle = t === null || t.category === originCategory;
      if (el && first && !reducedMotion() && sameAisle) {
        // Drop the lift transform (settle re-derives the delta from rects) but
        // hand the element off to `settle` — cleanup must NOT strip it.
        el.style.transform = '';
        el.style.pointerEvents = '';
        settlingId = id; // suppress the list's own FLIP for this row
        rowEl = null;
        commit(t);
        settle(el, first, id);
      } else if (el && first && !reducedMotion() && t) {
        // Cross-aisle: the row is destroyed in its source aisle and re-created in
        // the target, so we can't settle the same node. Instead fly a fixed ghost
        // clone from the release rect to the new slot, hiding the real row until
        // it lands. `settlingId` suppresses the list's crossfade flight for this
        // row so there is no double animation.
        rowEl = null;
        settlingId = id;
        ghostId = id;
        ghostSettle(el, first, id, t);
      } else {
        // Reduced motion, no target, or no element: instant, clean placement.
        el?.classList.remove('lifted');
        if (el) {
          el.style.transform = '';
          el.style.pointerEvents = '';
          el.style.willChange = '';
        }
        commit(t);
      }
      // The pointerup will be followed by a click on the handle's button;
      // swallow it so a drag never toggles the item.
      swallowNextClick();
    }
    cleanup();
  }

  /**
   * Hand-rolled FLIP settle for the just-dropped row. `first` is its on-screen
   * rect at release (lifted transform included); `el` is the keyed node Svelte
   * re-slots into place. After the reorder renders, we measure the row's resting
   * rect (Last), jump it back to the release delta with no transition, then
   * transition to identity — so it travels FROM the pointer TO its new slot
   * instead of teleporting to its origin slot first. The list's own FLIP is
   * suppressed for this row (via `settlingId`) so the two never double up.
   */
  function settle(el: HTMLElement, first: DOMRect, id: string): void {
    const finish = (): void => {
      el.style.transition = '';
      el.style.transform = '';
      el.style.willChange = '';
      el.classList.remove('lifted');
      if (settlingId === id) settlingId = null;
    };
    // Wait for Svelte to render the new order, then measure + animate next frame.
    void tick().then(() => {
      requestAnimationFrame(() => {
        const last = el.getBoundingClientRect();
        const dx = first.left - last.left;
        const dy = first.top - last.top;
        if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) {
          finish();
          return;
        }
        // First: place the row back under the pointer, no transition.
        el.style.transition = 'none';
        el.style.transform = `translate(${dx}px, ${dy}px)`;
        void el.offsetWidth; // force the start frame before transitioning
        // Last: ease to the resting slot. Matches the sibling FLIP duration so the
        // dropped row and the rows parting for it settle in lockstep.
        requestAnimationFrame(() => {
          el.style.transition = `transform ${d(DUR.flip)}ms cubic-bezier(0.33, 1, 0.68, 1)`;
          el.style.transform = '';
          window.setTimeout(finish, d(DUR.flip) + 20);
        });
      });
    });
  }

  /**
   * Cross-aisle settle. The dragged row is about to be destroyed in its source
   * aisle and re-created in the target — two different keyed nodes — so a FLIP on
   * "the element" is impossible. Instead we detach a fixed-position clone at the
   * release rect and animate THAT to the new slot, while the real re-created row
   * is held invisible (`ghostId`) until the clone lands. The result: the row
   * appears to travel from the pointer into its new aisle, with no teleport and
   * no competing crossfade (suppressed via `settlingId`).
   *
   * `first` is the source row's on-screen rect at release (lift transform
   * included); `el` is that soon-to-be-destroyed source node, cloned for the
   * ghost. `t` is the committed drop slot.
   */
  function ghostSettle(el: HTMLElement, first: DOMRect, id: string, t: DropTarget): void {
    // Build the flying clone from the lifted row so it matches pixel-for-pixel.
    const ghost = el.cloneNode(true) as HTMLElement;
    ghost.classList.add('lifted');
    ghost.style.position = 'fixed';
    ghost.style.margin = '0';
    ghost.style.left = `${first.left}px`;
    ghost.style.top = `${first.top}px`;
    ghost.style.width = `${first.width}px`;
    ghost.style.height = `${first.height}px`;
    ghost.style.transform = '';
    ghost.style.pointerEvents = 'none';
    ghost.style.zIndex = '50';
    ghost.style.willChange = 'transform';
    document.body.appendChild(ghost);

    const done = (): void => {
      ghost.remove();
      if (ghostId === id) ghostId = null; // reveal the real row
      if (settlingId === id) settlingId = null;
    };

    // Commit AFTER the ghost exists so the source row's destruction (and the
    // target row's birth, held invisible) never flashes an un-ghosted frame.
    commit(t);

    // Wait for the re-render, then measure the real (hidden) row's resting rect
    // and ease the ghost from the release point onto it.
    void tick().then(() => {
      requestAnimationFrame(() => {
        const real = document.querySelector<HTMLElement>(`[data-drag-id="${id}"] .row`);
        const last = real?.getBoundingClientRect();
        if (!last) {
          done();
          return;
        }
        const dx = last.left - first.left;
        const dy = last.top - first.top;
        if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) {
          done();
          return;
        }
        ghost.style.transition = `transform ${d(DUR.flip)}ms cubic-bezier(0.33, 1, 0.68, 1)`;
        requestAnimationFrame(() => {
          ghost.style.transform = `translate(${dx}px, ${dy}px)`;
          window.setTimeout(done, d(DUR.flip) + 20);
        });
      });
    });
  }

  function commit(t: { category: string; index: number } | null): void {
    if (!t || !item) return;
    const groups = deps.getGroups();
    const origin = groups.find((g) => g.category === originCategory);
    const originIdx = origin?.items.findIndex((i) => i.id === item!.id) ?? -1;
    // No-op: dropped back into its own slot in its own aisle.
    if (t.category === originCategory && t.index === originIdx) return;

    const list = (groups.find((g) => g.category === t.category)?.items ?? []).filter(
      (i) => i.id !== item!.id,
    );
    const key = midpointSortKey(list[t.index - 1]?.sort_key, list[t.index]?.sort_key);
    const category = t.category !== originCategory ? t.category : undefined;
    void deps.reorder(item.id, key, category).catch(() => {});
  }

  function onCancel(ev: PointerEvent): void {
    if (ev.pointerId !== pointerId) return;
    cleanup();
  }

  function cleanup(): void {
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    window.removeEventListener('pointercancel', onCancel);
    window.removeEventListener('scroll', onScroll, { capture: true } as EventListenerOptions);
    clearTimeout(longPress);
    if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = 0;
    }
    if (handleEl) {
      try {
        handleEl.releasePointerCapture(pointerId);
      } catch {
        /* capture may already be gone */
      }
    }
    if (rowEl) {
      rowEl.classList.remove('lifted');
      rowEl.style.transform = '';
      rowEl.style.pointerEvents = '';
      rowEl.style.willChange = '';
    }
    item = null;
    rowEl = null;
    handleEl = null;
    pointerId = -1;
    armed = false;
    lifted = false;
    geom = [];
    committedCategory = '';
    committedIndex = -1;
    target = null;
    draggingId = null;
    dropCategory = null;
    originCategoryReactive = null;
    gapHeight = 0;
  }

  function start(it: Item, category: string, ev: PointerEvent, el: HTMLElement): void {
    if (armed || lifted) return; // one drag at a time
    // Only the primary button for mouse; touch/pen always allowed.
    if (ev.pointerType === 'mouse' && ev.button !== 0) return;
    item = it;
    originCategory = category;
    rowEl = el;
    handleEl = ev.currentTarget as HTMLElement | null;
    pointerId = ev.pointerId;
    startX = ev.clientX;
    startY = ev.clientY;
    armed = true;
    lifted = false;
    target = null;
    committedCategory = '';
    committedIndex = -1;
    // Capture on the handle so every move/up lands here even if the pointer
    // slips off the (transformed) row.
    try {
      handleEl?.setPointerCapture(pointerId);
    } catch {
      /* setPointerCapture unsupported (e.g. jsdom) — window listeners suffice */
    }
    window.addEventListener('pointermove', onMove, { passive: false });
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onCancel);
    if (ev.pointerType === 'touch') {
      longPress = setTimeout(lift, LONGPRESS_MS);
    }
  }

  return {
    get draggingId() {
      return draggingId;
    },
    get dropCategory() {
      return dropCategory;
    },
    get target() {
      return target;
    },
    get originCategory() {
      return originCategoryReactive;
    },
    get settlingId() {
      return settlingId;
    },
    get ghostId() {
      return ghostId;
    },
    get gapHeight() {
      return gapHeight;
    },
    start,
  };
}
