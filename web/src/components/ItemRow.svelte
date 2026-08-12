<script lang="ts">
  import { Spring } from 'svelte/motion';
  import type { Item } from '../lib/types';
  import {
    d,
    DUR,
    EASE_CSS,
    staggerDelay,
    SQUASH_SPRING,
    SQUASH_DIP,
    reducedMotion,
  } from '../lib/motion';
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
  const glowDelay = $derived(staggerDelay(index));

  // A freshly-added (non-ring) item gets a brief peach left-edge glint so you
  // can see your add land in its aisle. One-shot on mount.
  //
  // Reduced motion is handled in CSS (`.row.fresh::before { display: none }`),
  // not here, and that is the right way round: `reducedMotion()` is a live read
  // but not a reactive one, so a JS gate answers with whatever the preference
  // was when this row last rendered — a shopper who turns the setting on
  // mid-shop keeps getting glints until the row happens to re-render. A media
  // query has no such lag.
  const justAdded = $derived(
    item.source !== 'ring' && Date.now() - Date.parse(item.created_at) < 2000,
  );

  // Small spring squash on check-off: snap down, spring back to rest. Shallow
  // and firmly damped — this is the acknowledgement under the thumb, not the
  // message; the strike-through and the colour carry that.
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
      squash.set(SQUASH_DIP, { instant: true });
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
    el.style.transition = `transform ${d(DUR.swipeBack)}ms ${EASE_CSS.swipeBack}`;
    el.style.transform = 'translateX(0)';
    clearFields();
    window.setTimeout(() => {
      el.style.transition = '';
      el.style.transform = '';
      el.classList.remove('swiping');
    }, d(DUR.swipeBack) + 20);
  }

  function commit(dir: SwipeDir): void {
    committing = true;
    swallowNextClick();
    navigator.vibrate?.(10);
    if (dir === 'left') {
      // Slide the row fully off toward the trash, then hand off to the store
      // (the swipe slide and the row's own collapse read as one motion).
      //
      // The handler and the item are captured NOW, while the pointer event is
      // still on the stack and this row's each-block is unambiguously live.
      // Both are prop getters reaching back into that block — AisleGroup hands
      // `onSwipeLeft` down through a `{@const}` — and the timer below fires
      // after the row has been checked, moved and possibly unmounted. Reading
      // them from inside it would be reading a branch that is on its way out,
      // and a left-swipe that silently fails to delete is the worst outcome
      // this gesture has.
      const fire = onSwipeLeft;
      const target = item;
      const el = contentEl;
      if (el && !reducedMotion()) {
        el.style.transition = `transform ${d(DUR.swipeCommit)}ms ${EASE_CSS.swipeCommit}`;
        el.style.transform = 'translateX(-100%)';
        window.setTimeout(() => fire?.(target), Math.round(d(DUR.swipeCommit) * 0.6));
      } else {
        fire?.(target);
      }
    } else {
      // Basket: snap the content home instantly, then toggle — the check-off
      // treatment (squash, strike, and the row collapsing where it stands)
      // carries it from there.
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
  style="--strike-dur: {d(DUR.check)}ms; --strike-ease: {EASE_CSS.check}; --glow-dur: {d(
    DUR.glow,
  )}ms; --glow-delay: {glowDelay}ms; --lift-dur: {DUR.lift}ms"
  onpointerdown={onSwipeDown}
>
  <!-- Action affordances revealed behind the row body as it slides; aria-hidden,
       keyboard users reach the same actions via the ✕ button / basket toggle. -->
  <div class="swipe-field basket" bind:this={basketFieldEl} aria-hidden="true">
    <!-- A tick, not a basket: the gesture checks the item off. The basket mark
         names the place things end up; this names the act. -->
    <Icon name="check" size={20} stroke={2} />
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
    /* Only ever runs on the way DOWN: .row.lifted replaces this transition with
       its own, so the row snaps to its card width the instant you pick it up,
       and eases back out to full bleed when the class comes off at the end of
       the drop settle. Without it the row pops 24px wider in a single frame
       right as it lands, which is the last thing you see and so the thing you
       remember. Reduced motion strips it globally (app.css). */
    transition: margin 140ms ease;
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
    /* --shelf-gutter is the app column's side gutter, handed down by the
       edge-to-edge shelf so the row's content keeps the left edge it had when
       the list was an inset card. It lands on this content layer rather than on
       .row because the action fields behind it are positioned to .row's padding
       box and have to reach the true edges of the column. Both the shelf and the
       basket drawer publish it, because both cancel the gutter. The fallback to
       0 is for a row with no such host above it — a bare ItemRow in a test, or
       any future container that doesn't cancel the column gutter in the first
       place and so has none to hand back. */
    padding: var(--row-edge-pad) calc(8px + var(--shelf-gutter, 0px)) var(--row-edge-pad)
      calc(4px + var(--shelf-gutter, 0px));
    width: 100%;
    box-sizing: border-box;
    touch-action: pan-y;
  }
  /* Opaque only while swiping, and opaque in whatever the host's ground is:
     base on the shelf, the drawer's darker recess in the basket. Painting a flat
     base here would flash a lighter slab across the drawer's rows. */
  .swipe-content.swiping {
    background: var(--row-ground, var(--ctp-base));
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
  /* The field itself is deliberately full-bleed on the edge-to-edge shelf — it
     is the row, and a tint stopping short of the column edge would read as a
     stripe rather than as the row's own background. Only the glyph takes the
     shelf gutter, so it surfaces where the chip and the ✕ it stands in for sit
     rather than hard against the screen edge. */
  .swipe-field.basket {
    justify-content: flex-start;
    padding-left: calc(20px + var(--shelf-gutter, 0px));
    background: color-mix(in srgb, var(--accent) 14%, transparent);
    color: var(--accent);
  }
  .swipe-field.trash {
    justify-content: flex-end;
    padding-right: calc(20px + var(--shelf-gutter, 0px));
    background: color-mix(in srgb, var(--ctp-red) 14%, transparent);
    color: var(--ctp-red);
  }
  /* Lifted-for-drag: raised off the shelf onto an elevated surface, kept above
     its neighbours. The transform (follow + scale) is driven inline by the drag
     controller. Only the box-shadow eases in, so the lift reads as a gentle
     pick-up.

     Under reduced motion nothing travels — the controller sends scale 1 — but
     the shadow still eases, at app.css's 100ms. That is deliberate, not a leak:
     `box-shadow` is on that block's `transition-property` list because
     elevation reads as tone rather than as travel, and a shadow that simply
     exists in one frame and not the previous one is a flicker, which is a worse
     thing to hand somebody who asked for less movement than a 100ms fade in of
     the same shadow. (There was a local `.row.lifted { transition: none }`
     here trying to kill it; the global list is `!important`, so it had no
     effect and was deleted rather than left standing as a false promise.)

     Three things make it a card rather than a row lying on another row, and it
     needed all three once the list went edge-to-edge:

     The inset. A full-bleed row is exactly as wide as the list, so its rounded
     corners have nowhere to be — and the controller's 1.03 lift scale then
     pushes them past the app column, where <main> (the scroller, so it clips
     horizontally whether it wants to or not) and the shelf's own `overflow:
     clip` shave them off square. Pulling the lifted row in by 12px gives the
     scale room and leaves the corners inside both boxes, which is what makes the
     shape read as picked up rather than as the list momentarily going wrong.
     12px is generous on purpose: the overhang is 1.5% of the row width, so the
     worst case is the widest column (4.2px of clearance left at 560px, 6.9px at
     375px). It is the only thing holding the corners — ListView used to back it
     up with an `overflow-clip-margin` on the shelf, and that was dropped once
     measurement showed the inset never gets close to needing it. Margin, not
     transform: the controller owns the transform.

     The outline, drawn a pixel inside its own box so nothing can crop it. It is
     the card edge the shelf gave up.

     The shadow, stepped up to --shadow-lift, because at this width the gentle
     resting-card shadow simply disappears under the row. */
  .row.lifted {
    z-index: 5;
    margin-inline: 12px;
    background: var(--row-ground, var(--ctp-base));
    border-radius: var(--radius);
    outline: var(--hairline);
    outline-offset: -1px;
    box-shadow: var(--shadow-lift);
    /* Written inline from DUR.lift by the markup above, so the one motion table
       owns this number too. Not routed through d(): see the note above about
       the shadow being the one thing a lift keeps under reduced motion. */
    transition: box-shadow var(--lift-dur, 160ms) ease;
  }
  /* The chip doubles as the drag handle. `grab` cursor and `touch-action:none`
     so a touch starting on it begins a (long-press) lift instead of scrolling. */
  .chip.handle {
    cursor: grab;
    touch-action: none;
  }
  .toggle {
    flex: 1 1 auto;
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
    /* The row's tap target. It is `align-items: center`, so it takes its height
       from its own content — the chip plus this padding — and clears the 44px
       one-handed floor on its own at both densities (dense: 32 + 2×7 = 46). The
       padding steps down 8px → 7px with density; the row's edge padding is a
       separate lever that changes the row and not this, spelled out at
       --row-edge-pad in app.css. */
    padding: var(--row-pad-y) 8px;
    background: none;
    border: none;
    border-radius: var(--radius);
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  /* The chip: a quiet rounded square one surface step above the ground the row
     is lying on, with a text-coloured 2px line icon on a 24px grid. */
  .chip {
    flex: 0 0 auto;
    width: var(--row-chip);
    height: var(--row-chip);
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
  /* Name over note, packed tight enough that the pair still fits inside the
     chip's box. Row height is max(chip, this stack) + padding, so keeping the
     stack under the chip means a note costs no height at all and the list keeps
     one even rhythm whether or not an item carries one. */
  .labels {
    display: flex;
    flex-direction: column;
    min-width: 0;
    gap: 0;
  }
  .name {
    color: var(--ctp-text);
    font-size: var(--row-name-size);
    /* Tighter than the document's 1.4, and the requirement is FIT, not clear:
       a name and a note stacked have to stay inside the chip's height, or a
       noted row is taller than an unnoted one and the list loses its rhythm.
       At 1.15 the pair is 19.6 + 16.1 = 35.7px inside a 36px chip (dense:
       17.3 + 15.0 against 32px, within a pixel of it); at 1.4 it is 43.4px and
       overruns by the better part of a line. Still room for descenders. */
    line-height: 1.15;
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
    /* Written inline from DUR.check, and it matters that it is as short as it
       is: the strike is drawn right under the thumb that just tapped, where
       the eye is already looking, and anything slower reads as the row
       thinking about it. The hold that keeps this row on its shelf long enough
       to see it is that same duration (see lib/hold.svelte.ts). */
    transition:
      background-size var(--strike-dur, 150ms) var(--strike-ease, ease),
      color var(--strike-dur, 150ms) var(--strike-ease, ease);
  }
  .note {
    color: var(--ctp-subtext0);
    font-size: var(--row-note-size);
    line-height: 1.15;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  /* De-emphasise a checked row through the strike + a hollowed, dimmed chip —
     NOT by dropping the text contrast. The name stays at subtext1, which clears
     4.5:1 on the drawer's recessed ground in every flavour (Latte 4.73:1 is the
     tight one; Frappé 8.24, Macchiato 9.66, Mocha 10.59), so "in the basket"
     items remain legible. */
  .row.checked .name {
    color: var(--ctp-subtext1);
    background-size: 100% 1px;
  }
  /* Not subtext0 like an active row's note: a checked row lies on the basket
     drawer's recessed ground, a rung darker than the shelf, and subtext0 there
     measures 3.73:1 in Latte — under the floor. subtext1 puts it back at 4.73:1
     (and ≥6.7:1 in Frappé, Macchiato and Mocha). It lands on the name's colour,
     which is fine here: on a struck-through row the size difference carries the
     hierarchy and both lines are meant to be receding together. */
  .row.checked .note {
    color: var(--ctp-subtext1);
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
    /* The glint is removed outright rather than merely stopped. app.css's
       `animation: none !important` already stops the keyframes — but with the
       animation gone so is the `forwards` that was fading this bar out, and it
       sits there fully opaque and permanent. So the pseudo-element goes. This
       is also the ONLY gate on it: the JS that adds `.fresh` deliberately does
       not consult `reducedMotion()`, because that read is not reactive and a
       media query is.

       `.row.glow` needs nothing here for the opposite reason: its bare state
       is already the transparent one its keyframes end on, so the global
       `animation: none` leaves nothing behind and a local restatement of it
       would only be a second place to maintain.

       Nor does the strike-through: app.css drops `background-size` from the
       transition list (the line is drawn, so it is travel) while keeping the
       colour shift at 100ms, so a checked row still visibly changes state
       without anything sliding. */
    .row.fresh::before {
      display: none;
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
