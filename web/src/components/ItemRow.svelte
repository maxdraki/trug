<script lang="ts">
  import { Spring } from 'svelte/motion';
  import type { Item } from '../lib/types';
  import { d, DUR, STAGGER, SQUASH_SPRING, reducedMotion } from '../lib/motion';
  import {
    axisLock,
    swipeCommit,
    revealProgress,
    resistedDelta,
    type Axis,
    type SwipeDir,
    AXIS_LOCK_PX,
    DISTANCE_FRACTION,
    VELOCITY_THRESHOLD,
    MIN_FLICK_PX,
    REDUCED_MOTION_TRIGGER_PX,
  } from '../lib/swipe';
  import { resolveIcon } from '../lib/resolveIcon';
  import { swallowNextClick } from '../lib/dom';
  import Icon from './Icon.svelte';

  let {
    item,
    onToggle,
    onRemove,
    onOpen,
    onDragStart,
    onSwipeLeft,
    onSwipeRight,
    pending = false,
    index = 0,
  }: {
    item: Item;
    onToggle: (id: string) => void;
    onRemove: (id: string) => void;
    onOpen?: (item: Item) => void;
    /** When present, the chip acts as a drag-to-reorder handle. */
    onDragStart?: (item: Item, ev: PointerEvent, rowEl: HTMLElement) => void;
    /** Commit a left-swipe (delete). When absent, left-swipe is disabled. */
    onSwipeLeft?: (item: Item) => void;
    /** Commit a right-swipe (send to basket). When absent, right-swipe is disabled. */
    onSwipeRight?: (item: Item) => void;
    pending?: boolean;
    /** Position within its group, used to stagger the ring-arrival glow. */
    index?: number;
  } = $props();

  // The row element the drag controller lifts and follows the pointer with.
  let rowEl = $state<HTMLElement>();
  // The inner element the SWIPE gesture translates (drag transforms `.row`; swipe
  // transforms this content layer so the action fields behind it stay put).
  let contentEl = $state<HTMLElement>();
  let basketFieldEl = $state<HTMLElement>();
  let trashFieldEl = $state<HTMLElement>();

  const checked = $derived(item.status === 'checked');
  const monogram = $derived((item.name.trim()[0] ?? '?').toUpperCase());

  // Shared chip-resolution path (rows + recents tray). Resolves the item's icon
  // slug, a legacy emoji glyph, or a name-derived slug fallback.
  const icon = $derived(resolveIcon(item.icon, item.name));

  // A ring item added within the last few seconds gets a one-shot accent glow
  // on mount. Older ring items (already on the list at load) do not re-glow.
  const justArrived = $derived(
    item.source === 'ring' && Date.now() - Date.parse(item.created_at) < 4000,
  );
  const glowDelay = $derived(d(index * STAGGER));

  // A freshly-added (non-ring) item gets a brief peach left-edge glint so you
  // can see your add land in its aisle. One-shot on mount; reduced motion skips
  // it (the arrival transition already conveys placement).
  const justAdded = $derived(
    item.source !== 'ring' &&
      !reducedMotion() &&
      Date.now() - Date.parse(item.created_at) < 2000,
  );

  // Small spring squash on check-off: snap down, spring back to rest.
  const squash = new Spring(1, SQUASH_SPRING);
  let prevChecked: boolean | undefined;
  $effect(() => {
    const now = checked;
    if (prevChecked === undefined) {
      prevChecked = now;
      return;
    }
    if (now === prevChecked) return;
    prevChecked = now;
    if (now && !reducedMotion()) {
      squash.set(0.9, { instant: true });
      squash.set(1);
    }
  });

  function toggle() {
    navigator.vibrate?.(10);
    onToggle(item.id);
  }

  // --- Swipe-to-act (right → basket, left → delete) ---------------------------
  //
  // Same performance discipline as the drag controller: one direct-DOM transform
  // write per animation frame on the captured element, no reactive state touched
  // per pointer sample, and all the decision maths lives in the pure `swipe`
  // helpers (unit-tested) so the live follow and the release commit agree.

  // Right-swipe (to basket) is offered on ACTIVE rows only — a checked row is
  // already in the basket, so its right-swipe rubber-bands and never commits (the
  // basket icon would otherwise promise an action the gesture can't perform).
  const rightEnabled = $derived(!!onSwipeRight && !checked);
  const leftEnabled = $derived(!!onSwipeLeft); // any row may be deleted

  let pointerId = -1;
  let startX = 0;
  let startY = 0;
  let lastX = 0;
  let lastT = 0;
  let velocity = 0;
  let axis: Axis = null;
  let swiping = false;
  let committing = false;
  let rowWidth = 0;
  let latestDx = 0;
  let rafId = 0;

  function setField(el: HTMLElement | undefined, show: boolean, amt: number): void {
    if (!el) return;
    el.style.opacity = show ? String(amt) : '0';
    el.style.transform = `scale(${0.6 + 0.4 * amt})`;
  }

  function paintFields(dx: number): void {
    const rightDir = dx > 0;
    const amt = revealProgress(dx, rowWidth, DISTANCE_FRACTION);
    setField(basketFieldEl, rightDir && rightEnabled, amt);
    setField(trashFieldEl, !rightDir && leftEnabled, amt);
  }

  function clearFields(): void {
    setField(basketFieldEl, false, 0);
    setField(trashFieldEl, false, 0);
  }

  function frame(): void {
    rafId = 0;
    if (!swiping || !contentEl) return;
    const dx = resistedDelta(latestDx, rightEnabled, leftEnabled);
    contentEl.style.transform = `translateX(${dx}px)`;
    paintFields(dx);
  }

  function springBack(): void {
    if (!contentEl) return;
    const el = contentEl;
    if (reducedMotion()) {
      el.style.transform = '';
      el.classList.remove('swiping');
      clearFields();
      return;
    }
    el.style.transition = `transform ${d(DUR.slide)}ms cubic-bezier(0.22, 1, 0.36, 1)`;
    el.style.transform = 'translateX(0)';
    clearFields();
    window.setTimeout(() => {
      el.style.transition = '';
      el.style.transform = '';
      el.classList.remove('swiping');
    }, d(DUR.slide) + 20);
  }

  function commit(dir: SwipeDir): void {
    committing = true;
    swallowNextClick();
    navigator.vibrate?.(10);
    if (dir === 'left') {
      // Slide the row fully off toward the trash, then hand off to the store
      // (its removal `out:` flight and the swipe slide read as one motion).
      const el = contentEl;
      if (el && !reducedMotion()) {
        el.style.transition = `transform ${d(DUR.flip)}ms cubic-bezier(0.33, 1, 0.68, 1)`;
        el.style.transform = 'translateX(-100%)';
        window.setTimeout(() => onSwipeLeft?.(item), Math.round(d(DUR.flip) * 0.6));
      } else {
        onSwipeLeft?.(item);
      }
    } else {
      // Basket: snap the content home instantly, then toggle — the existing
      // check-off treatment (squash + crossfade flight to the basket) carries it.
      if (contentEl) {
        contentEl.style.transition = '';
        contentEl.style.transform = '';
        contentEl.classList.remove('swiping');
      }
      clearFields();
      onSwipeRight?.(item);
    }
    cleanup();
  }

  function onMove(ev: PointerEvent): void {
    if (ev.pointerId !== pointerId) return;
    const dx = ev.clientX - startX;
    const dy = ev.clientY - startY;
    if (!swiping) {
      if (axis === null) {
        axis = axisLock(dx, dy, AXIS_LOCK_PX);
        if (axis === null) return;
        if (axis === 'vertical') {
          cleanup(); // release to native scroll / drag-nothing
          return;
        }
        // Horizontal: enter swipe mode. Capture so the follow survives the
        // pointer slipping off the (translated) row; preventDefault below keeps
        // page scroll from fighting the slide.
        swiping = true;
        try {
          rowEl?.setPointerCapture(pointerId);
        } catch {
          /* jsdom / unsupported — window listeners suffice */
        }
        contentEl?.classList.add('swiping');
      }
    }
    if (!swiping) return;
    ev.preventDefault();
    const now = performance.now();
    const dt = now - lastT;
    if (dt > 0) velocity = (ev.clientX - lastX) / dt;
    lastX = ev.clientX;
    lastT = now;
    if (reducedMotion()) {
      // No slide to drag a threshold out of: a modest travel commits outright.
      const eff = resistedDelta(dx, rightEnabled, leftEnabled);
      const dir: SwipeDir = eff > 0 ? 'right' : 'left';
      const allowed = dir === 'right' ? rightEnabled : leftEnabled;
      if (allowed && Math.abs(eff) >= REDUCED_MOTION_TRIGGER_PX) commit(dir);
      return;
    }
    latestDx = dx;
    if (!rafId) rafId = requestAnimationFrame(frame);
  }

  function onUp(ev: PointerEvent): void {
    if (ev.pointerId !== pointerId) return;
    if (!swiping) {
      cleanup();
      return;
    }
    const dx = resistedDelta(ev.clientX - startX, rightEnabled, leftEnabled);
    const dir = swipeCommit(dx, velocity, rowWidth, {
      distanceFraction: DISTANCE_FRACTION,
      velocityThreshold: VELOCITY_THRESHOLD,
      minFlick: MIN_FLICK_PX,
    });
    const enabled = dir === 'right' ? rightEnabled : dir === 'left' ? leftEnabled : false;
    if (dir && enabled) {
      commit(dir);
      return;
    }
    springBack();
    cleanup();
  }

  function onCancel(ev: PointerEvent): void {
    if (ev.pointerId !== pointerId) return;
    if (swiping) springBack();
    cleanup();
  }

  function cleanup(): void {
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    window.removeEventListener('pointercancel', onCancel);
    if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = 0;
    }
    try {
      rowEl?.releasePointerCapture(pointerId);
    } catch {
      /* capture may already be gone */
    }
    swiping = false;
    committing = false;
    axis = null;
    pointerId = -1;
    velocity = 0;
    latestDx = 0;
  }

  function onSwipeDown(ev: PointerEvent): void {
    if (committing) return;
    if (!onSwipeLeft && !onSwipeRight) return;
    if (ev.pointerType === 'mouse' && ev.button !== 0) return;
    // The chip is the reorder handle; the ⓘ/✕ are buttons. Neither starts a swipe.
    const t = ev.target as HTMLElement | null;
    if (t?.closest('.chip') || t?.closest('.icon-btn')) return;
    pointerId = ev.pointerId;
    startX = ev.clientX;
    startY = ev.clientY;
    lastX = ev.clientX;
    lastT = performance.now();
    velocity = 0;
    axis = null;
    swiping = false;
    rowWidth = rowEl?.offsetWidth ?? 0;
    if (contentEl) contentEl.style.transition = ''; // track immediately
    window.addEventListener('pointermove', onMove, { passive: false });
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onCancel);
  }
</script>

<!-- The pointerdown here only arms the swipe gesture — a pointer-only enhancement
  whose every action is already keyboard-reachable (the ✕ button deletes; the
  row toggle sends to / from the basket), so the row needs no interactive role of
  its own. -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  bind:this={rowEl}
  class="row"
  class:checked
  class:pending
  class:glow={justArrived}
  class:fresh={justAdded}
  class:swipeable={!!onSwipeLeft || !!onSwipeRight}
  style="--strike-dur: {d(DUR.check)}ms; --glow-dur: {d(DUR.glow)}ms; --glow-delay: {glowDelay}ms"
  onpointerdown={onSwipeDown}
>
  <!-- Action affordances revealed behind the row body as it slides; aria-hidden,
       keyboard users reach the same actions via the ✕ button / basket toggle. -->
  <div class="swipe-field basket" bind:this={basketFieldEl} aria-hidden="true">
    <Icon name="basket" size={20} stroke={2} />
  </div>
  <div class="swipe-field trash" bind:this={trashFieldEl} aria-hidden="true">
    <Icon name="trash" size={20} stroke={2} />
  </div>
  <div class="swipe-content" bind:this={contentEl}>
  <button class="toggle" type="button" onclick={toggle} aria-pressed={checked}>
    <span
      class="chip {icon.kind}"
      class:handle={!!onDragStart}
      aria-hidden="true"
      style="transform: scale({squash.current})"
      onpointerdown={onDragStart ? (e) => onDragStart(item, e, rowEl!) : undefined}
    >
      {#if icon.kind === 'line'}
        <Icon name={icon.slug} size={20} stroke={2} />
      {:else if icon.kind === 'glyph'}
        {icon.glyph}
      {:else}
        {monogram}
      {/if}
    </span>
    <span class="labels">
      <span class="name">
        {item.name}
        {#if item.source === 'ring'}
          <span class="badge" role="img" aria-label="added by the ring" title="added by the ring">
            <Icon name="wand" size={12} stroke={2} />
          </span>
        {/if}
      </span>
      {#if item.note}<span class="note">{item.note}</span>{/if}
    </span>
  </button>

  {#if onOpen}
    <button class="icon-btn" type="button" onclick={() => onOpen?.(item)} aria-label="Edit {item.name}">
      <Icon name="pencil" size={15} stroke={1.75} />
    </button>
  {:else}
    <button class="icon-btn" type="button" onclick={() => onRemove(item.id)} aria-label="Remove {item.name}">
      <Icon name="x" size={15} stroke={1.75} />
    </button>
  {/if}
  </div>
</div>

<style>
  .row {
    position: relative;
    display: flex;
    align-items: center;
  }
  .row.pending {
    opacity: 0.55;
  }
  /* Swipeable rows clip their sliding content so the action fields behind it are
     only ever seen through the gap the slide opens, never overhanging the row. */
  .row.swipeable {
    overflow: hidden;
  }
  /* The layer the swipe gesture translates. Transparent at rest (so the shelf
     surface, fresh-add tick and glow show through); it takes an opaque surface
     only while swiping, to occlude the action field it slides across. Vertical
     panning stays native (touch-action: pan-y) so a vertical drag scrolls the
     list; horizontal is handled in JS with axis-lock. */
  .swipe-content {
    position: relative;
    z-index: 1;
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 4px 8px 4px 4px;
    width: 100%;
    box-sizing: border-box;
    touch-action: pan-y;
  }
  .swipe-content.swiping {
    background: var(--ctp-base);
    user-select: none;
  }
  /* Behind-row action fields: basket (accent) revealed on a right-swipe, anchored
     to the leading edge the row uncovers; trash (red) on a left-swipe, anchored
     to the trailing edge. Subtle color-mix tints per DESIGN.md (accent = still to
     get; red = the sanctioned destructive status colour). Opacity + scale are
     driven inline as the commit threshold nears. */
  .swipe-field {
    position: absolute;
    inset: 0;
    z-index: 0;
    display: flex;
    align-items: center;
    opacity: 0;
    pointer-events: none;
  }
  .swipe-field.basket {
    justify-content: flex-start;
    padding-left: 20px;
    background: color-mix(in srgb, var(--accent) 14%, transparent);
    color: var(--accent);
  }
  .swipe-field.trash {
    justify-content: flex-end;
    padding-right: 20px;
    background: color-mix(in srgb, var(--ctp-red) 14%, transparent);
    color: var(--ctp-red);
  }
  /* Lifted-for-drag: raised off the shelf onto an elevated surface with a soft
     shadow, kept above its neighbours. The transform (follow + scale) is driven
     inline by the drag controller. Only the box-shadow eases in, so the lift
     reads as a gentle pick-up; reduced motion drops that transition (and the
     controller sends scale 1) for an instant, motionless lift. */
  .row.lifted {
    z-index: 5;
    background: var(--ctp-base);
    border-radius: var(--radius);
    box-shadow: var(--shadow-2, var(--shadow-1));
    transition: box-shadow var(--lift-dur, 160ms) ease;
  }
  /* The chip doubles as the drag handle. `grab` cursor and `touch-action:none`
     so a touch starting on it begins a (long-press) lift instead of scrolling. */
  .chip.handle {
    cursor: grab;
    touch-action: none;
  }
  @media (prefers-reduced-motion: reduce) {
    .row.lifted {
      transition: none;
    }
  }
  .toggle {
    flex: 1 1 auto;
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
    padding: 8px;
    background: none;
    border: none;
    border-radius: var(--radius);
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  /* The chip: a quiet rounded square one surface step above the card, with a
     text-coloured 2px line icon on a 24px grid. */
  .chip {
    flex: 0 0 auto;
    width: 36px;
    height: 36px;
    border-radius: var(--radius);
    display: grid;
    place-items: center;
    background: color-mix(in srgb, var(--ctp-surface0) 55%, var(--ctp-base));
    color: var(--ctp-text);
    font-size: 19px;
    line-height: 1;
    transform-origin: center;
  }
  .chip.monogram {
    background: color-mix(in srgb, var(--ctp-lavender) 16%, var(--ctp-base));
    color: var(--ctp-lavender);
    font-family: var(--font-display);
    font-weight: 500;
    font-size: 15px;
  }
  /* Accent scope (variant B): an ACTIVE item's line-icon chip glyph is the
     accent — "still to get". The chip square background stays ink; `color`
     inherits into the child <Icon> svg (stroke="currentColor"). Checked rows
     are overridden below by `.row.checked .chip` (higher specificity). */
  .chip.line {
    color: var(--accent);
  }
  .labels {
    display: flex;
    flex-direction: column;
    min-width: 0;
    gap: 1px;
  }
  .name {
    color: var(--ctp-text);
    font-size: 17px;
    font-weight: 500;
    letter-spacing: -0.006em;
    display: flex;
    align-items: center;
    gap: 8px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    /* Strike-through as a gradient underline that draws left→right on check. */
    background-image: linear-gradient(var(--ctp-overlay0), var(--ctp-overlay0));
    background-repeat: no-repeat;
    background-position: 0 58%;
    background-size: 0% 1px;
    transition:
      background-size var(--strike-dur, 260ms) ease,
      color var(--strike-dur, 260ms) ease;
  }
  .note {
    color: var(--ctp-subtext0);
    font-size: 14px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .badge {
    flex: 0 0 auto;
    display: inline-flex;
    color: var(--ctp-overlay1);
  }
  /* De-emphasise a checked row through the strike + a hollowed, dimmed chip —
     NOT by dropping the text contrast. The name stays at subtext1 (>=4.5:1 in
     both Latte and Mocha) so "in the basket" items remain legible. */
  .row.checked .name {
    color: var(--ctp-subtext1);
    background-size: 100% 1px;
  }
  .row.checked .note {
    color: var(--ctp-subtext0);
  }
  .row.checked .chip {
    background: transparent;
    border: var(--hairline);
    color: var(--ctp-overlay1);
    opacity: 0.5;
    filter: grayscale(1);
  }
  /* One-shot accent glow for a freshly-arrived ring item. */
  .row.glow {
    animation: ring-glow var(--glow-dur, 350ms) ease-out var(--glow-delay, 0ms) both;
  }
  @keyframes ring-glow {
    0% {
      background: color-mix(in srgb, var(--accent) 24%, transparent);
      box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 45%, transparent);
    }
    100% {
      background: transparent;
      box-shadow: inset 0 0 0 1px transparent;
    }
  }
  /* Fresh-add glint: a peach left-edge tick that fades over 2s, so you see the
     new row land in its aisle. `forwards` leaves it transparent at rest. */
  .row.fresh::before {
    content: '';
    position: absolute;
    left: 0;
    top: 4px;
    bottom: 4px;
    width: 2px;
    border-radius: 2px;
    background: var(--accent);
    animation: fresh-tick 2000ms ease-out forwards;
  }
  @keyframes fresh-tick {
    0% {
      opacity: 1;
    }
    100% {
      opacity: 0;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .row.glow {
      animation: none;
    }
    .row.fresh::before {
      display: none;
    }
    .name {
      transition: none;
    }
  }
  .icon-btn {
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border-radius: var(--radius);
    border: none;
    background: none;
    color: var(--ctp-overlay0);
    cursor: pointer;
    transition: color 120ms ease, background 120ms ease;
  }
  .icon-btn:hover {
    background: var(--ctp-surface0);
    color: var(--ctp-text);
  }
</style>
