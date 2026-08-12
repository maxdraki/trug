<script lang="ts">
  import { Spring } from 'svelte/motion';
  import { flip } from 'svelte/animate';
  import { untrack } from 'svelte';
  import type { CatalogEntry } from '../lib/types';
  import { d, DUR, PRESS_SPRING, PRESS_DIP, settleFlip, reducedMotion } from '../lib/motion';

  let {
    top,
    activeNames = new Set<string>(),
    onPick,
  }: {
    /** Fetches the most-added catalogue entries (typically `api.top`). */
    top: (n: number) => Promise<CatalogEntry[]>;
    /** Normalised names already on the list, hidden from the grid. */
    activeNames?: Set<string>;
    onPick: (displayName: string) => void;
  } = $props();

  let entries = $state<CatalogEntry[]>([]);

  $effect(() => {
    let cancelled = false;
    // Deliberate over-fetch: `activeNames` filters client-side, so by the end of
    // a big shop twenty-odd of these are already on the list and would leave the
    // tray half-empty. Two rows plus an expandable full set has more appetite
    // than the old eight-pill cap did, hence the larger surplus.
    top(48)
      .then((res) => {
        if (!cancelled) entries = res;
      })
      .catch((err) => {
        if (cancelled) return;
        // The tray hides itself when empty, so a failing catalogue endpoint is
        // otherwise indistinguishable from "you have no regulars yet" — and it
        // is fetched once per mount, so it stays blank for the whole session.
        // Hiding it is still the right behaviour; being silent about why isn't.
        console.error('[trug] could not load frequently-added items', err);
        entries = [];
      });
    return () => {
      cancelled = true;
    };
  });

  const candidates = $derived(entries.filter((e) => !activeNames.has(e.name_norm)));

  /* How far a pill may slide to its new slot before it is asked to just be
     there instead — the canonical statement of the number `settleFlip`'s
     `maxTravel` exists for. Picking a pill re-wraps the whole tray, and the
     ones that fall back onto the previous row cross its entire width doing it:
     318px in 250ms, measured, which is a smear rather than a move you can
     follow, and it is not even a move the shopper made — the text reflowed. A
     pill sliding along its own row, or dropping to the row below, is under this
     and still animates. */
  const PILL_MAX_TRAVEL = 120;

  /* Two rows of pills — however many pills that turns out to be. Pills are
     sized by their own text, so which one lands on row three is an outcome of
     the layout, not a number we can pick in advance: the tray has to be read
     back after the browser has wrapped it. `cap` is how many pills survived
     that reading; Infinity means "no ceiling known", which is also what a
     browser-less environment reports (every offsetTop is 0, so the whole set
     reads as a single row) — the tray then shows everything rather than
     hiding itself behind a counter. */
  let cap = $state(Number.POSITIVE_INFINITY);
  let expanded = $state(false);
  let trayEl = $state<HTMLDivElement | null>(null);
  let moreEl = $state<HTMLButtonElement | null>(null);

  /* Bumped whenever something outside the entry list invalidates the reading.
     Two things do: a new tray width, and a density change. */
  let remeasure = $state(0);
  /* Plain, not $state, on purpose: comparing the observed width must not become
     a dependency of anything, or the comparison would schedule its own rerun. */
  let lastWidth = -1;

  /* Identifies the arrangement a reading was taken of: change any part of it
     and the reading is stale. Names are joined on a character a normalised
     name cannot contain, so no two sets of entries can spell the same key. */
  const layoutKey = $derived(
    `${remeasure}:${expanded}:${candidates.map((e) => e.name_norm).join('\u0000')}`,
  );
  let measuredKey = $state<string | null>(null);

  /* While a reading is outstanding the tray renders every pill: you can only
     find out where row three starts on a tray that is holding all of them. This
     happens inside a single flush, before the browser paints, so the unclipped
     state is never seen. */
  const showAll = $derived(expanded || measuredKey !== layoutKey);

  /* The height the tray last settled at, and the pin that holds it there while
     the next reading is taken.

     A reading is invisible but it is not free: for that one flush the tray is
     three or four rows instead of two, and everything below it in <main> sits
     ~170px lower. Nothing paints it — but the list below is measuring itself in
     that same flush. Svelte's `animate:flip` on the aisle rows takes its "from"
     rects while this tray is unclipped and its "to" rects after it has
     collapsed, concludes that every row in the list moved, and eases the whole
     shelf back from a displacement that never happened. That is the reported
     "all the items spin like a one-armed bandit" on every check-off: checking an
     item drops its name out of `activeNames`, which changes `candidates`, which
     invalidates the reading.

     Pinning the tray to the height it already had, and clipping what hangs out
     of it, means a reading costs the document no height at all — the rows below
     measure the same geometry before and after, so their FLIP has nothing to
     animate. Only the height is pinned: pills wrap by width, so the reading
     itself is unaffected.

     Null until the first reading has settled, which is before there is any list
     motion to corrupt — and null forever where there is no layout engine to
     measure it (jsdom), because the `height > 0` guard below never lets a zero
     through. That guard is doing real work rather than warding off the
     impossible: a zero, whether from jsdom or from a measurement taken while
     the box is mid-layout, would pin the tray to no height at all — and the pin
     feeds the next reading, so the tray would stay shut and never measure its
     way back out. Null means "nothing to hold still", which is the honest
     answer in both cases. */
  let settledHeight = $state<number | null>(null);
  const pinnedHeight = $derived(showAll && !expanded ? settledHeight : null);

  $effect(() => {
    // Settled state only. Measuring mid-reading would either bake the taller
    // unclipped reading in as the pin, or — once pinned — feed the pin its own
    // output and freeze the tray at whatever height it first happened to have.
    if (showAll || !trayEl) return;
    const height = trayEl.getBoundingClientRect().height;
    if (height > 0) settledHeight = height;
  });

  const hiddenCount = $derived(Math.max(0, candidates.length - cap));
  /* Mid-reading the real count isn't known yet, and the counter is only in the
     DOM to have its width taken — so label it with the largest number it could
     end up showing, and reserve room for that. */
  const moreCount = $derived(showAll ? candidates.length : hiddenCount);

  $effect(() => {
    const key = layoutKey;
    const el = trayEl;
    // `trayEl` is read as a dependency, so a null one simply means "not mounted
    // yet" — the reading is retaken the moment the node lands.
    if (!el || untrack(() => measuredKey) === key) return;
    const pills = [...el.querySelectorAll<HTMLElement>('.pill:not(.more)')];
    if (!expanded) cap = twoRowCap(pills, el, moreEl);
    measuredKey = key;
  });

  $effect(() => {
    const el = trayEl;
    // No ResizeObserver (jsdom, and very old browsers): the tray keeps whatever
    // it measured at mount, which is right until the window changes shape.
    if (!el || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver((records) => {
      const width = Math.round(records[records.length - 1].contentRect.width);
      // Width only. Clipping the tray changes its HEIGHT, so an observer that
      // reacted to height would be watching the result of its own last reading
      // and would never settle.
      if (width === lastWidth) return;
      lastWidth = width;
      remeasure += 1;
    });
    observer.observe(el);
    return () => observer.disconnect();
  });

  $effect(() => {
    // Density is the other thing that resizes a pill, and the width observer
    // above is blind to it: pills borrow --row-note-size (14px comfortable,
    // 13px dense), so every one of them changes width while the TRAY's width
    // does not change at all. Measured at 515px: comfortable fits nine and caps
    // at nine; switched to dense, ten fit but the cap stayed at nine and the
    // counter lied. The other direction is the damaging one — a cap measured at
    // dense is too many pills for comfortable, so the surplus wraps onto a
    // third row and the two-row guarantee this whole loop exists to enforce
    // quietly breaks.
    //
    // The pin goes with it: `settledHeight` was measured at the old density and
    // is the wrong height to hold the tray at while it re-reads. Nothing needs
    // holding still here anyway — a density change relayouts the entire list,
    // so there is no FLIP to protect from a phantom displacement.
    if (typeof MutationObserver === 'undefined') return;
    const root = document.documentElement;
    const observer = new MutationObserver(() => {
      settledHeight = null;
      remeasure += 1;
    });
    observer.observe(root, { attributes: true, attributeFilter: ['data-density'] });
    return () => observer.disconnect();
  });

  /** The gap the layout actually used, read off the first pair of pills sharing
   * a row. Taken from the rendered result rather than from `gap` in the
   * stylesheet so the arithmetic still holds if that value ever changes. */
  function usedGap(pills: HTMLElement[], tops: number[]): number {
    for (let i = 1; i < pills.length; i += 1) {
      if (tops[i] === tops[i - 1]) {
        return pills[i].offsetLeft - (pills[i - 1].offsetLeft + pills[i - 1].offsetWidth);
      }
    }
    return 0;
  }

  /** How many of `pills` fit on the first two wrapped rows, with room kept at
   * the end of row two for the counter. */
  function twoRowCap(
    pills: HTMLElement[],
    tray: HTMLElement,
    more: HTMLElement | null,
  ): number {
    const tops = pills.map((p) => p.offsetTop);
    // Distinct offsets are the rows: variable-width pills mean a row holds
    // however many it holds, and only the browser knows.
    const rows = [...new Set(tops)].sort((a, b) => a - b);
    if (rows.length < 3) return Number.POSITIVE_INFINITY;

    let fit = tops.findIndex((t) => t >= rows[2]);
    // The counter sits at the end of row two, so it needs room of its own: if
    // it would shove the last pill onto row three, that pill joins the count.
    const room = tray.clientWidth;
    const counter = more?.offsetWidth ?? 0;
    const gap = usedGap(pills, tops);
    while (fit > 1) {
      const last = pills[fit - 1];
      if (last.offsetLeft + last.offsetWidth + gap + counter <= room) break;
      fit -= 1;
    }
    return fit;
  }

  // Press-scale spring: the pressed cell dips and springs back to rest.
  const press = new Spring(1, PRESS_SPRING);
  let pressed = $state<string | null>(null);

  function pressDown(key: string) {
    if (reducedMotion()) return;
    pressed = key;
    press.set(PRESS_DIP, { instant: true });
  }
  async function pressUp() {
    if (pressed === null) return;
    const key = pressed;
    // Keep the transform bound to this cell while the spring settles back to
    // rest — clearing `pressed` immediately would unbind mid-flight and snap.
    await press.set(1);
    if (pressed === key) pressed = null;
  }
</script>

{#if candidates.length}
  <h2 class="tray-head">Frequently added</h2>
  <!-- `overflow: hidden` rides with the pin rather than living in the
       stylesheet: it exists to hide the extra rows a READING unfolds inside the
       pinned box, and there is nothing to clip at any other time. -->
  <div
    class="tray"
    bind:this={trayEl}
    style={pinnedHeight === null ? '' : `height: ${pinnedHeight}px; overflow: hidden`}
  >
    <!-- Every candidate stays in the DOM and the clipped ones are hidden by
         class, rather than being sliced out of the list: a reading can only
         find where row three starts on a tray that is holding all of them. -->
    {#each candidates as e, i (e.name_norm)}
      <!-- A picked pill leaves the tray in the same frame it is picked —
           nothing animates it out, so nothing holds its slot — and the pills
           after it close up under this flip. -->
      <button
        class="pill"
        class:clipped={!showAll && i >= cap}
        type="button"
        animate:settleFlip={{ duration: d(DUR.flip), maxTravel: PILL_MAX_TRAVEL }}
        style={pressed === e.name_norm ? `transform: scale(${press.current})` : ''}
        onpointerdown={() => pressDown(e.name_norm)}
        onpointerup={pressUp}
        onpointerleave={pressUp}
        onpointercancel={pressUp}
        onclick={() => onPick(e.display_name)}
      >
        {e.display_name}
      </button>
    {/each}
    {#if showAll || hiddenCount > 0}
      <!-- The same control both ways: the tap that opened the tray is the tap
           that closes it, in the place you last touched. A separate collapse
           control elsewhere would be a second thing to find. -->
      <button
        class="pill more"
        type="button"
        bind:this={moreEl}
        aria-expanded={expanded}
        aria-label={expanded ? 'Show fewer frequently added items' : `Show ${moreCount} more`}
        onclick={() => (expanded = !expanded)}
      >
        {expanded ? 'Show less' : `+${moreCount}`}
      </button>
    {/if}
  </div>
{/if}

<style>
  .tray-head {
    margin: 0;
    padding: 4px 4px 8px;
    font-family: var(--font-display);
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--ctp-subtext0);
  }
  /* A slim tray, not a shelf card: pills that wrap onto a second line rather
     than scrolling sideways, so the shortcuts on offer are the ones you can
     see — nothing hidden past a right edge that gives no sign there is more
     behind it. Capped at two rows — past that, scanning the tray costs more
     than typing the name — with the rest a tap away behind a counter that
     says how many. */
  .tray {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    padding: 0 0 12px;
    /* Makes the tray the offsetParent, so the pills' offsetLeft/offsetTop are
       measured against it and not against some scrolling ancestor. */
    position: relative;
    /* So the height pinned during a reading (see `pinnedHeight`) is the height
       the tray actually occupied, padding included, rather than 12px short of
       it. Nothing else here sets a width or height, so this is inert otherwise. */
    box-sizing: border-box;
  }
  /* Each pill is its item's full name — never truncated. Sized by its text, so
     "Sea Salt" stays small and "Extra-virgin Olive Oil" simply takes the room
     it needs. Accent-tinted: these are things you'll likely need, the same
     reading the accent carries on active rows. */
  .pill {
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    min-height: 34px;
    padding: 6px 12px;
    border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
    border-radius: 999px;
    background: color-mix(in srgb, var(--accent) 5%, var(--ctp-base));
    /* The accent tints the border and fill, never the label. Accent-tinted text
       failed 4.5:1 on Latte for most accents (peach 3.39, yellow 3.01 at rest;
       worse on hover), and contrast must not depend on which accent you picked. */
    color: var(--ctp-text);
    /* The note size, so a pill sits a step below the item names it feeds — and
       follows density down with them. */
    font-size: var(--row-note-size);
    font-weight: 500;
    line-height: 1.2;
    /* A name longer than the tray wraps between pills, never inside one, so
       without a ceiling a single long name would push the page sideways. */
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    cursor: pointer;
    transition: background-color 120ms ease, border-color 120ms ease, color 120ms ease;
  }
  .pill:hover {
    border-color: color-mix(in srgb, var(--accent) 55%, transparent);
    background: color-mix(in srgb, var(--accent) 10%, var(--ctp-base));
  }
  /* Past the second row. `display: none` rather than a visual trick: these are
     real buttons, and a hidden shortcut must be out of the tab order and out of
     the screen reader's list, not merely out of sight. */
  .pill.clipped {
    display: none;
  }
  /* The counter is a control, not a shortcut, so it steps back: no accent tint,
     just the muted outline of the tray it belongs to. */
  .more {
    border-color: var(--ctp-surface1);
    background: transparent;
    color: var(--ctp-subtext0);
    font-variant-numeric: tabular-nums;
  }
  .more:hover {
    border-color: var(--ctp-surface2);
    background: color-mix(in srgb, var(--ctp-surface0) 60%, transparent);
  }
</style>
