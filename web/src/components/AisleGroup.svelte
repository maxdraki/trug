<script lang="ts">
  import { slide } from 'svelte/transition';
  import { flip } from 'svelte/animate';
  import type { AnimationConfig } from 'svelte/animate';
  import type { Item } from '../lib/types';
  import ItemRow from './ItemRow.svelte';
  import Icon from './Icon.svelte';
  import { CATEGORY_ICON } from '../lib/walkOrder';
  import { d, DUR, arriveRow, sendItem, keyOf } from '../lib/motion';

  import type { DragController } from '../lib/drag.svelte';
  import { rowGapShift, aisleReserve } from '../lib/drag.svelte';

  let {
    category,
    items,
    pendingIds,
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
</script>

<section
  class="aisle"
  class:drop-target={isDropTarget}
  data-drag-aisle={category}
  transition:slide={{ duration: d(DUR.slide) }}
>
  <h2 data-drag-label>
    <span class="cat-ico"><Icon name={CATEGORY_ICON[category] ?? 'shopping-bag'} size={18} stroke={1.75} /></span>
    {category}
  </h2>
  <div
    class="rows"
    class:reserving={dragging}
    style={dragging ? `margin-bottom: ${reserve}px; --gap-dur: ${gapDur}ms` : ''}
  >
    {#each items as item, i (item.id)}
      {@const shift = shiftFor(item, i)}
      {@const suppressed = drag?.settlingId === item.id}
      {@const hidden = drag?.ghostId === item.id}
      <div
        class="row-wrap"
        class:gapping={dragging}
        data-drag-id={item.id}
        style={`${dragging ? `transform: translateY(${shift}px); --gap-dur: ${gapDur}ms;` : ''}${hidden ? 'opacity: 0;' : ''}`}
        animate:dragFlip={{ duration: d(DUR.flip) }}
        in:arriveRow={{ key: keyOf(item.name), source: item.source, index: i, suppress: suppressed }}
        out:sendItem={{ key: keyOf(item.name), suppress: suppressed }}
      >
        <ItemRow
          {item}
          {onToggle}
          {onRemove}
          {onOpen}
          onDragStart={drag ? (it, ev, el) => drag.start(it, category, ev, el) : undefined}
          {onSwipeLeft}
          {onSwipeRight}
          index={i}
          pending={pendingIds.has(item.id)}
        />
      </div>
    {/each}
  </div>
</section>

<style>
  /* Not a card — a continuous run of shelf on the single base surface owned by
     ListView. No background, border, radius or shadow of its own; adjacent
     aisles are divided by a hairline (see ListView's `.shelf > * + *`). */
  .aisle {
    display: flex;
    flex-direction: column;
  }
  /* The Space Grotesk aisle label sits directly on its shelf-edge rule; rows
     flow beneath it. */
  h2 {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 0;
    padding: 10px 20px 8px;
    font-family: var(--font-display);
    font-size: 13px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    /* subtext1 clears 4.5:1 on the base surface in both Latte and Mocha. */
    color: var(--ctp-subtext1);
    /* A whisper of accent tints the shelf label. */
    background: color-mix(in srgb, var(--accent) 3%, transparent);
    /* The shelf edge the rows sit on. */
    border-bottom: var(--hairline);
    transition: color 120ms ease, border-color 120ms ease;
  }
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
     live drop slot. Duration comes from the inline --gap-dur (routed through
     motion.ts d()), so reduced motion collapses the transition to instant. */
  .row-wrap.gapping {
    transition: transform var(--gap-dur, 0ms) cubic-bezier(0.2, 0, 0, 1);
  }
  /* Cross-aisle space reservation: the target aisle grows and the origin aisle
     shrinks by one row-height so the opening gap has real space to live in and
     the vacated slot closes up — no row ever overhangs into the next aisle. */
  .rows.reserving {
    transition: margin-bottom var(--gap-dur, 0ms) cubic-bezier(0.2, 0, 0, 1);
  }
</style>
