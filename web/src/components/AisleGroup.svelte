<script lang="ts">
  import { flip } from 'svelte/animate';
  import type { AnimationConfig } from 'svelte/animate';
  import type { Item } from '../lib/types';
  import ItemRow from './ItemRow.svelte';
  import Icon from './Icon.svelte';
  import { CATEGORY_ICON } from '../lib/walkOrder';
  import { d, DUR, EASE_CSS, arriveRow, leaveRow, unfold, fold } from '../lib/motion';

  import type { DragController } from '../lib/drag.svelte';
  import { rowGapShift, aisleReserve } from '../lib/drag.svelte';

  let {
    category,
    items,
    pendingIds,
    heldIds = new Set<string>(),
    drag,
    onToggle,
    onRemove,
    onOpen,
    onSwipeLeft,
    onSwipeRight,
  }: {
    category: string;
    items: Item[];
    pendingIds: Set<string>;
    /** Rows the store has already checked off, still here to play the strike. */
    heldIds?: Set<string>;
    drag?: DragController;
    onToggle: (id: string) => void;
    onRemove: (id: string) => void;
    onOpen: (item: Item) => void;
    onSwipeLeft?: (item: Item) => void;
    onSwipeRight?: (item: Item) => void;
  } = $props();

  // This aisle is the live recategorise target — brighten its shelf-edge rule.
  const isDropTarget = $derived(drag?.dropCategory === category);

  // A drag is in flight and its live drop slot is known — rows part to preview it.
  const dragging = $derived(!!drag?.draggingId && !!drag?.target);
  // Where the dragged row sits within *this* aisle (−1 if it started elsewhere).
  const originIndex = $derived(
    drag?.draggingId ? items.findIndex((i) => i.id === drag.draggingId) : -1,
  );
  // Gap transitions route through motion.ts `d()` so reduced motion is instant.
  const gapDur = $derived(d(DUR.gap));
  // ...and the curve comes from the same table, published alongside it. The
  // literal used to be spelled out twice in the stylesheet below, which is
  // exactly the JS/CSS drift the dual EASE / EASE_CSS tables exist to stop.
  const gapEase = EASE_CSS.gap;

  // Cross-aisle: origin and target are different groups. The per-row translateY
  // shift only *rearranges* rows within an aisle — it never changes the aisle's
  // box height, so on its own it lets a target row slide down past the aisle's
  // last hairline and overlap the next shelf header (the reported "beer pushed
  // into Household" artifact). We fix that at the aisle level: the TARGET aisle
  // grows by one row-height to hold the opening gap (pushing everything below it
  // down), and the ORIGIN aisle shrinks by one row-height to absorb the lifted
  // row's vacated slot (pulling everything below it up). The two are equal and
  // opposite, so aisles between/after them net to zero movement — only the
  // origin's and target's own rows shift, exactly as the boundary contract
  // requires.
  const reserve = $derived(
    dragging && drag?.target
      ? aisleReserve(
          category,
          { originCategory: drag.originCategory ?? '', targetCategory: drag.target.category },
          drag.gapHeight,
        )
      : 0,
  );

  // Vertical shift (px) that opens the gap: 0 for the dragged row (the pointer
  // drives its transform) and for rows unaffected by the current drop slot.
  function shiftFor(item: Item, i: number): number {
    if (!dragging || !drag?.target || item.id === drag.draggingId) return 0;
    return rowGapShift(
      category,
      i,
      {
        originCategory: drag.originCategory ?? '',
        originIndex,
        targetCategory: drag.target.category,
        targetIndex: drag.target.index,
      },
      drag.gapHeight,
    );
  }

  // FLIP for row motion — but the row currently playing its post-release settle
  // is driven by the drag controller from the pointer-release position, so the
  // list's own FLIP (which would start from the row's ORIGIN slot) must sit that
  // one out. Without this the dropped row double-animates: the visible "drops
  // from its original position" artifact.
  function dragFlip(
    node: Element,
    dims: { from: DOMRect; to: DOMRect },
    params: { duration: number },
  ): AnimationConfig {
    if (drag?.settlingId && node.getAttribute('data-drag-id') === drag.settlingId) {
      return { duration: 0 };
    }
    return flip(node, dims, params);
  }

  // The same question the FLIP above asks, as a plain function rather than a
  // `{@const}` — and it has to be, because transition params are read at a
  // moment when a `{@const}` lies. Svelte marks an each-branch INERT *before*
  // calling `transition.out()`, and a `$derived` owned by an inert branch does
  // not recompute: it warns (`derived_inert`) and hands back the previous
  // frame's value. So `out:leaveRow` was being told whether the row was
  // settling as of the frame before the drop. A function on the component
  // instance has no branch to be inert, so it answers for now. `in:` is read
  // while the branch is live and was never wrong, but it is the same landmine
  // one edit away from going off, so it reads this too.
  function isSettling(id: string): boolean {
    return drag?.settlingId === id;
  }
</script>

<section
  class="aisle"
  class:drop-target={isDropTarget}
  data-drag-aisle={category}
  in:unfold
  out:fold
>
  <h2 data-drag-label>
    <span class="cat-ico"><Icon name={CATEGORY_ICON[category] ?? 'shopping-bag'} size={18} stroke={1.75} /></span>
    {category}
    <span class="count">{items.length}</span>
  </h2>
  <div
    class="rows"
    class:reserving={dragging}
    style={dragging
      ? `margin-bottom: ${reserve}px; --gap-dur: ${gapDur}ms; --gap-ease: ${gapEase}`
      : ''}
  >
    {#each items as item, i (item.id)}
      {@const shift = shiftFor(item, i)}
      {@const hidden = drag?.ghostId === item.id}
      <!-- Held: checked off, still here for the length of the strike (DUR.check)
           while it draws — see lib/hold.svelte.ts. It
           stays tappable (a second tap un-checks it) but takes no drag and no
           swipe, because a gesture on a row that is already leaving cannot mean
           anything reliable. Nor does it take the `pending` dim: that fade would
           run over the top of the confirmation this hold exists to show, and the
           row wears it again the moment it lands in the basket. -->
      {@const held = heldIds.has(item.id)}
      <div
        class="row-wrap"
        class:gapping={dragging}
        class:lifting={drag?.draggingId === item.id}
        data-drag-id={item.id}
        style={`${dragging ? `transform: translateY(${shift}px); --gap-dur: ${gapDur}ms; --gap-ease: ${gapEase};` : ''}${hidden ? 'opacity: 0;' : ''}`}
        animate:dragFlip={{ duration: d(DUR.flip) }}
        in:arriveRow={{ source: item.source, index: i, suppress: isSettling(item.id) }}
        out:leaveRow={{ suppress: isSettling(item.id) }}
      >
        <ItemRow
          {item}
          {onToggle}
          {onRemove}
          {onOpen}
          onDragStart={drag && !held ? (it, ev, el) => drag.start(it, category, ev, el) : undefined}
          onSwipeLeft={held ? undefined : onSwipeLeft}
          onSwipeRight={held ? undefined : onSwipeRight}
          index={i}
          pending={pendingIds.has(item.id) && !held}
        />
      </div>
    {/each}
  </div>
</section>

<style>
  /* Not a card — a continuous run of shelf on the single base surface owned by
     ListView. No background, border, radius or shadow of its own; adjacent
     aisles are divided by a hairline (see ListView's `.shelf > section + section`). */
  .aisle {
    display: flex;
    flex-direction: column;
  }
  /* The Space Grotesk aisle label sits directly on its shelf-edge rule; rows
     flow beneath it.

     Sticky, because with the card edges gone this band is the only thing left
     saying which aisle you are looking at — scroll it away and a long Dairy run
     is just rows. It sticks to <main>, the app's one scrolling box (ListView's
     .shelf clips with `overflow: clip` rather than `hidden` precisely so it
     does not become a scroll container and capture this). The containing block
     is the .aisle section, so each band is pushed off by the next one instead of
     stacking up.

     z-index 2, deliberately between two things: above .swipe-content's 1, or the
     rows would scroll over their own label; below .row.lifted's 5, so a row you
     have picked up passes OVER the band rather than diving under it. */
  h2 {
    position: sticky;
    top: 0;
    z-index: 2;
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 0;
    padding: var(--aisle-head-pad);
    font-family: var(--font-display);
    font-size: var(--aisle-head-size);
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    /* subtext1 on the band's own ground, which is mantle (--band-fill), not the
       base the rows lie on: 5.14:1 in Latte, and Latte is the tight one — every
       dark flavour is far clear of the floor. */
    color: var(--ctp-subtext1);
    /* The only fill in the list body (see --band-fill). It also has to be opaque
       now that the band is sticky — a tint would let the rows it covers read
       straight through it. */
    background: var(--band-fill);
    /* The shelf runs edge-to-edge, so the label re-spends the column gutter
       (--shelf-gutter, published by ListView's .shelf) to stay on the same left
       edge it had inside the card. A transparent inline border, not padding:
       --aisle-head-pad is one shorthand owned by the density tokens, and adding
       to it here would mean restating its inline value in a second place that
       then drifts the next time density is tuned. The border box still paints
       the tint and the rule, so both keep reaching both edges of the column. */
    border-inline: var(--shelf-gutter, 0px) solid transparent;
    /* The shelf edge the rows sit on. */
    border-bottom: var(--hairline);
    transition: color 120ms ease, border-color 120ms ease;
  }
  /* `.count` — how much is left on this shelf, sitting straight after the aisle
     name so the two read as one label — is styled in app.css, shared with the
     basket drawer's count. */
  /* Recategorise affordance: no accent colour — the shelf-edge hairline simply
     brightens (subtext1) and the label steps up to full text, so the aisle
     under the dragged row reads as the drop target. */
  .aisle.drop-target h2 {
    color: var(--ctp-text);
    border-bottom-color: var(--ctp-subtext1);
  }
  /* Shelf-label icons carry the accent (variant B): the aisle marker is part of
     the "still to get" family, matching the active-row chip glyphs. */
  .cat-ico {
    display: inline-flex;
    color: var(--accent);
  }
  .rows {
    display: flex;
    flex-direction: column;
  }
  .rows > :global(.row-wrap + .row-wrap) {
    border-top: var(--hairline);
  }
  /* While a drag is in flight, rows ease as they part around / close over the
     live drop slot. Duration AND curve come from the inline --gap-dur /
     --gap-ease, both written from motion.ts's tables (the duration through
     `d()`, so reduced motion collapses this to instant). The curve used to be
     a literal here and in `.rows.reserving` below — two copies of a number
     that lives in CURVE.gap, which is one edit away from the gap and the space
     it opens into easing differently. */
  .row-wrap.gapping {
    transition: transform var(--gap-dur, 0ms) var(--gap-ease, linear);
  }
  /* Lift the *wrapper* of the dragged row, not just the row. Every wrapper takes
     an inline transform while a drag is in flight, and a transform makes a
     stacking context — which seals ItemRow's `.row.lifted { z-index: 5 }` inside
     a box that itself ranks as z-index auto. The sticky shelf band (z-index 2)
     then paints straight over the row you are holding: you drag it up to the
     label and it slides underneath. Raising the wrapper past the band is what
     puts the lifted row back on top, where a thing in your hand belongs. */
  .row-wrap.lifting {
    position: relative;
    z-index: 6;
  }
  /* Cross-aisle space reservation: the target aisle grows and the origin aisle
     shrinks by one row-height so the opening gap has real space to live in and
     the vacated slot closes up — no row ever overhangs into the next aisle. */
  .rows.reserving {
    transition: margin-bottom var(--gap-dur, 0ms) var(--gap-ease, linear);
  }
</style>
