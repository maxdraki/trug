<script lang="ts">
  import { tick, onDestroy } from 'svelte';
  import { flip } from 'svelte/animate';
  import type { Item } from '../lib/types';
  import type { Store } from '../lib/store.svelte';
  import AisleGroup from './AisleGroup.svelte';
  import ItemRow from './ItemRow.svelte';
  import ItemSheet from './ItemSheet.svelte';
  import EmptyState from './EmptyState.svelte';
  import Toast from './Toast.svelte';
  import Icon from './Icon.svelte';
  import Logo from '../lib/Logo.svelte';
  import {
    d,
    DUR,
    EASE,
    EASE_CSS,
    arriveRow,
    leaveRow,
    staggerDelay,
    unfold,
    fold,
  } from '../lib/motion';
  import { createHoldSet, heldSlot, withHeldRows } from '../lib/hold.svelte';

  import type { DragController } from '../lib/drag.svelte';

  let {
    store,
    walkOrder,
    drag,
    onUpdate,
  }: {
    store: Store;
    walkOrder: string[];
    drag?: DragController;
    onUpdate: (id: string, fields: { note?: string | null; category?: string }) => Promise<void> | void;
  } = $props();

  let editing = $state<Item | null>(null);
  /* What an undo is FOR, which decides what it may displace.
     `check` is the cheap one: the row is sitting in the basket drawer, one tap
     from being back, so it is recoverable whether or not this notice survives.
     `delete` and `clear` are not — a deleted row takes its note with it, and a
     cleared basket takes a pile of them — so a check-off never displaces one. */
  type UndoKind = 'check' | 'delete' | 'clear';
  /* ONE undo notice, not two. There used to be a second — the cleared basket's
     — rendered by an `{:else if}` behind this one, keeping its own independent
     five seconds. Since a check-off is always armed later than a live clear and
     both windows are 5s, the hidden one ALWAYS expired first and always while
     masked: the shopper watched a notice offering twelve rows back turn, with
     no transition and no signal, into one about the item they just ticked off,
     and the twelve were gone. One slot means a displaced undo is displaced
     visibly, and a notice on screen is always the notice that is live. */
  let swipeUndo = $state<{
    text: string;
    onUndo: () => void;
    timer: ReturnType<typeof setTimeout>;
    kind: UndoKind;
    /** The row it speaks for, so a later gesture on that row can retire it. */
    id?: string;
  } | null>(null);
  // True only while a "clear checked" batch is on its way out. It does two
  // jobs, and the second one is what makes the first work at all: it applies
  // the staggered farewell to a bulk clear (a single uncheck or delete gets the
  // plain 200ms collapse), and it holds the drawer's own block mounted while
  // that plays. Svelte skips a LOCAL transition when the block enclosing it is
  // destroyed — and clearing the basket empties `store.checked`, which destroys
  // exactly that block. Measured: clearing nine rows produced zero row
  // animations. The cascade had never once run.
  let clearing = $state(false);
  let clearingTimer: ReturnType<typeof setTimeout> | undefined;

  // The basket drawer, closed on every load and deliberately not persisted. By
  // the end of a shop the done pile is most of the list, and a remembered "open"
  // would push the handful of items you still need off the bottom of the screen
  // — which is the one thing this list must never do. Opening it is a per-session
  // act of looking something up, not a preference.
  let basketOpen = $state(false);
  let drawerEl = $state<HTMLElement>();

  /**
   * Opening the drawer grows it downwards, off the bottom of the screen, so the
   * thing you just asked to see isn't there. `block: 'nearest'` is exactly the
   * behaviour wanted and the browser already knows it: a drawer that fits gets
   * scrolled the minimum to bring its last row into view, and one taller than
   * the screen aligns its top edge instead — landing the header at the top with
   * the rest to scroll through. Aimed at the whole drawer, header included, so
   * the tall case doesn't put the header off the top.
   *
   * Run every frame WHILE the drawer unfolds rather than once after it: the
   * drawer is growing, so a single call has to wait for the final height, and
   * waiting read as two separate movements — the drawer opened, the list sat
   * still for a beat, then it scrolled (measured: unfolded by 260ms, scroll
   * didn't start until ~360ms, settled ~570ms). Re-aiming each frame keeps the
   * drawer's bottom edge pinned to the screen as it grows, so the list rises to
   * meet it and the whole thing is one gesture that ends when the slide does.
   * `behavior: 'auto'` because the slide is already the animation; a smooth
   * scroll on top would be a second, slower one fighting it.
   *
   * With one ceiling on it: the chase is only a gesture while the distance is
   * one you can follow. Past a viewport and a half it is a smear, so that case
   * cuts instead — one jump, once the drawer has finished unfolding.
   *
   * Open only: collapsing must not yank the viewport.
   *
   * Only the CHASE is abandoned when the shopper scrolls or touches the list —
   * it is a frame-by-frame pursuit, and the moment they say where they want to
   * be, continuing to drag the column somewhere else is fighting them. The cut
   * branch has no such listeners and is not meant to: it is one deliberate
   * landing, already scheduled, and the whole point of it is that the shopper
   * asked for a 43-item basket and gets taken to it. It checks only that the
   * drawer is still open when it fires (~340ms later); a shopper who has
   * scrolled away in the meantime is still taken to the drawer.
   */
  async function revealDrawer() {
    const el = drawerEl;
    const main = el?.closest('main');
    if (!el || !main) return;

    const bring = () => el.scrollIntoView({ block: 'nearest', behavior: 'auto' });
    // Not `slide`: that was the name of the imported transition this file used
    // four times in its own template, and shadowing it here read as if the
    // markup below were somehow using this number.
    const unfoldMs = d(DUR.expand);
    if (!unfoldMs) {
      // Reduced motion: no unfold to track, just land. But not until Svelte has
      // flushed the open — called straight from the click handler the drawer is
      // still only its header, which fits, so `nearest` bottom-aligns the handle
      // and leaves the pile below the fold. Measured: 619px short of the top
      // alignment a 42-item basket wants.
      await tick();
      bring();
      return;
    }

    // How far the list will have to travel, measured before it starts. A drawer
    // shorter than the screen only needs its bottom edge brought into view; one
    // taller than the screen is top-aligned instead, so the travel is however
    // far its header currently sits down the page.
    await tick();
    const box = el.getBoundingClientRect();
    const port = main.getBoundingClientRect();
    // The unfolding rows are the part that is about to arrive, and they are
    // exactly the part the drawer's own box does NOT yet include: the slide
    // clips them to a height of nearly zero, so `el.scrollHeight` at this
    // moment is a header and little else. Their full extent has to be added
    // back from the region's own overflow, or every basket looks small enough
    // to chase and the cap below never fires.
    const region = el.querySelector('.rows');
    const pending = region ? region.scrollHeight - region.clientHeight : 0;
    const grown = el.scrollHeight + Math.max(0, pending);
    const travel =
      grown <= main.clientHeight
        ? Math.max(0, box.top + grown - port.bottom)
        : Math.max(0, box.top - port.top);

    // Past a viewport and a half, chasing the unfold frame by frame stops being
    // one gesture and becomes the biggest movement in the app: opening a
    // 43-item basket ran the column 1867px in ~300ms, which is the same
    // unfollowable smear the check-off flight was, wearing a different hat.
    // Beyond that distance the honest thing is a cut — land once, at the end,
    // and let the shopper re-orient from a still page rather than from a blur.
    if (travel > main.clientHeight * 1.5) {
      setTimeout(() => {
        if (basketOpen) bring();
      }, unfoldMs + 40);
      return;
    }

    let stop = false;
    const abandon = () => (stop = true);
    main.addEventListener('wheel', abandon, { passive: true });
    main.addEventListener('touchstart', abandon, { passive: true });
    const done = () => {
      main.removeEventListener('wheel', abandon);
      main.removeEventListener('touchstart', abandon);
    };

    const started = performance.now();
    const step = () => {
      if (stop || !basketOpen) return done();
      bring();
      // A frame past the slide's end: the last growth frame still needs chasing.
      if (performance.now() - started < unfoldMs + 40) requestAnimationFrame(step);
      else done();
    };
    requestAnimationFrame(step);
  }

  function toggleBasket() {
    basketOpen = !basketOpen;
    if (basketOpen) revealDrawer();
  }

  // Rows whose check-off is still playing. The store has already moved them to
  // the basket; these keep rendering where they stood for the length of the
  // strike-through, then let go and collapse out through the usual `leaveRow`.
  const holds = createHoldSet();
  onDestroy(() => {
    holds.releaseAll();
    // Every timer this component owns has to die with it, not just the holds'.
    // `clearingTimer` flips `clearing` back off; left running it reaches for
    // state on a destroyed component 400-odd ms after the list has gone. The
    // undo timer does the same 5s later, and now that EVERY check-off arms one,
    // that is the most common timer the view owns rather than a rare one.
    clearTimeout(clearingTimer);
    if (swipeUndo) clearTimeout(swipeUndo.timer);
  });

  // One item is in exactly one place at a time: a held row still counts as its
  // aisle's, and reaches the basket only once it has actually left the shelf.
  const groups = $derived(withHeldRows(store.groups, holds.rows, walkOrder));
  const basket = $derived(
    holds.rows.length ? store.checked.filter((i) => !holds.ids.has(i.id)) : store.checked,
  );

  const empty = $derived(groups.length === 0 && basket.length === 0);

  function toggle(id: string, holdMs: number = d(DUR.check)) {
    // Where the row is NOW decides everything: found on a shelf, this tap is a
    // check-off and the row is held; anywhere else (a basket row, or a row
    // already held and tapped a second time) it is an uncheck, which holds
    // nothing. Read before the store moves it.
    // Read off the RENDERED groups, not the store's. The two differ by exactly
    // the rows that are mid-hold, and a slot counted in a list those rows are
    // missing from is a slot short: two rows checked off 40ms apart came back
    // in the wrong order, the aisle visibly swapping them under the thumb.
    // A live undo for THIS row is stale the moment the row is touched again —
    // check a row off and un-check it by hand and the toast would still read
    // "In the basket", with a button that now checks it off a second time.
    // Undo doing the opposite of undo is worse than no undo.
    if (swipeUndo?.id === id) clearSwipeUndo();
    const slot = heldSlot(groups, id);
    // Let go of any hold on this id first — a second tap inside the window is an
    // uncheck, and the store's answer is what should render.
    holds.release(id);
    // The mutation is never delayed: the optimistic move and the queued op both
    // happen on this tick, so the hold cannot lose a check-off or desync the
    // pending set. It is presentation, nothing more.
    store.toggle(id);
    if (!slot) return; // an uncheck: the row going back IS the undo
    // How long the row stays on its shelf wearing the check. A tap's
    // confirmation is the strike drawing, so the strike's own duration is the
    // window; a right-swipe's is the row completing the journey the thumb threw
    // it on, which takes longer. Held for the shorter one, the row would be
    // yanked off the shelf partway through its own slide.
    holds.hold(slot, holdMs);
    // Every check-off is offered back, however it was asked for. The GESTURE
    // decides how the row leaves — a swipe has a direction and momentum to
    // follow through, a tap has neither — but the undo answers the CONSEQUENCE,
    // and both gestures have exactly the same one. Offered to the swipe alone,
    // a mistap cost a hunt through a basket drawer that is collapsed by default.
    //
    // Routed back through this function rather than straight to `store.toggle`:
    // pressed inside the hold window it has to let the held copy GO as well as
    // uncheck the row, or the same id is keyed twice in one each-block. The
    // re-entry is safe — an uncheck finds no slot and returns above, so it
    // neither holds anything nor offers an undo of its own.
    // Never over something dearer. A check-off is recoverable from the drawer
    // whatever happens to this notice; a delete or a cleared basket is not.
    if (swipeUndo && swipeUndo.kind !== 'check') return;
    // Named, because "In the basket" is the same sentence about every row: tick
    // off Milk, Bread and Beer in one breath and the notice never changes, so
    // the shopper correcting the Milk mistap presses Undo and gets Beer. It is
    // also what makes the live region speak again — a status region whose text
    // is unchanged does not re-announce.
    showSwipeUndo(`${slot.item.name} in the basket`, () => toggle(id), 'check', id);
  }
  /** Every single-row delete, whatever gesture asked for it. The undo is not
   *  decoration: a delete also RETIRES the name's catalogue row, and only a
   *  re-add inside the server's stash window brings that shortcut back to the
   *  tray and to typeahead. Without one, a mistap quietly costs the name its
   *  whole history.
   *
   *  It takes the ROW rather than an id so there is never a delete it cannot
   *  offer back. Looking the id up in the store instead left one path with no
   *  undo — the sheet's Remove, for a row the store had meanwhile dropped (a
   *  partner's delete, an SSE removal racing the tap) — which is exactly the
   *  silent case this exists to close. Both callers are holding the row
   *  already; the sheet's may carry a note one save behind, which is a far
   *  better undo than none. */
  function deleteWithUndo(item: Item) {
    const { name } = item;
    const note = item.note ?? undefined;
    store.remove(item.id);
    showSwipeUndo(`Deleted ${name}`, () => store.add(name, note), 'delete');
  }
  function open(item: Item) {
    editing = item;
  }

  function clearChecked() {
    // A row still mid-hold is already checked in the store, so it is going in
    // this clear whether or not its strike has finished drawing. Let the holds
    // go first: otherwise the row is yanked off the shelf from under a playing
    // animation, and the snapshot the undo restores disagrees with the pile the
    // shopper watched leave.
    holds.releaseAll();
    const snapshot = store.checked.slice();
    if (!snapshot.length) return;
    // The cascade is only worth holding the drawer open for when the pile is
    // actually on screen: with the door shut there are no rows to see leave,
    // and pinning an emptied drawer there for another 400ms would be a pause
    // where the shopper expected the thing to be gone. Reduced motion says the
    // same thing for its own reasons.
    clearing = basketOpen && d(DUR.clear) > 0;
    clearTimeout(clearingTimer);
    if (clearing) {
      clearingTimer = setTimeout(
        () => (clearing = false),
        d(DUR.clear) + staggerDelay(snapshot.length - 1) + 60,
      );
    }
    store.clearChecked();
    showSwipeUndo(
      `Cleared ${snapshot.length} item${snapshot.length === 1 ? '' : 's'}`,
      () => {
        for (const it of snapshot) store.add(it.name, it.note ?? undefined);
      },
      'clear',
    );
  }

  function clearSwipeUndo() {
    if (!swipeUndo) return;
    clearTimeout(swipeUndo.timer);
    swipeUndo = null;
  }
  function showSwipeUndo(text: string, onUndo: () => void, kind: UndoKind, id?: string) {
    clearSwipeUndo();
    swipeUndo = { text, onUndo, kind, id, timer: setTimeout(() => (swipeUndo = null), 5000) };
  }
  function runSwipeUndo() {
    const u = swipeUndo;
    if (!u) return;
    clearTimeout(u.timer);
    // Cleared BEFORE the action, not after: `onUndo` may arm a notice of its
    // own, and nulling afterwards would drop that one on the floor while its
    // timer went on running — later blanking whatever unrelated undo happened
    // to be on screen when it fired.
    swipeUndo = null;
    // Does this still describe the world? A row can return to the shelf without
    // passing through any gesture of ours — the store rolling a failed check
    // back, or someone else un-checking it on their phone. Firing anyway would
    // check it off AGAIN: undo doing the exact opposite of undo. Then the only
    // honest thing left is to take the offer away, which is what the shopper
    // sees.
    if (u.kind === 'check' && !store.checked.some((i) => i.id === u.id)) return;
    u.onUndo();
  }

  // Left-swipe: delete, with a 5s undo. Undo re-adds by name/note through the
  // normal add path — its category re-resolves via the tier-0 / catalog lookup
  // and it gets a fresh id, so this restores the item, not the exact prior row.
  const swipeDelete = deleteWithUndo;
  // Right-swipe (active rows only): the same check-off a tap performs, and the
  // same undo. All this adds is the hold window — the slide's length plus a
  // frame's grace, because a transition does not start until the next style
  // recalc and so lands a frame AFTER a timer set on the same tick, leaving the
  // row pulled into its exit with the last frame unrun. `springBack` buys
  // itself the same margin. The grace goes INSIDE `d()`, so reduced motion
  // still holds nothing at all.
  function swipeBasket(item: Item) {
    toggle(item.id, d(DUR.swipeCommit + 20));
  }
</script>

<div class="list">
  {#if empty}
    <EmptyState />
  {:else}
    <div class="shelf">
      {#each groups as group (group.category)}
        <AisleGroup
          category={group.category}
          items={group.items}
          pendingIds={store.pendingIds}
          heldIds={holds.ids}
          {drag}
          onToggle={toggle}
          onOpen={open}
          onSwipeLeft={swipeDelete}
          onSwipeRight={swipeBasket}
        />
      {/each}
    </div>

    <!-- `|| clearing` holds this block mounted while the pile leaves. Without
         it the block is destroyed the instant `store.checked` empties, and
         svelte skips the rows' local outros — the staggered farewell below
         simply never played. -->
    {#if basket.length || clearing}
      <!-- Eased, not snapped. The drawer appearing and disappearing used to move
           ~80px in a single frame while the rows above were still animating,
           which read as the whole list lurching rather than one item moving. -->
      <section
        class="checked"
        bind:this={drawerEl}
        in:unfold
        out:fold
      >
        <div class="drawer-head">
          <h2>
            <!-- No `aria-controls`, deliberately. The region it would name is
                 mounted only while the drawer is open (see below — that is a
                 performance decision worth keeping), so on every page load it
                 pointed at an id that was not in the document, which is worse
                 for a screen reader than saying nothing. `aria-expanded` on
                 its own is valid, universally supported, and carries the state
                 that actually matters here. -->
            <button
              type="button"
              class="disclosure"
              aria-expanded={basketOpen}
              onclick={toggleBasket}
            >
              <span
                class="chev"
                class:open={basketOpen}
                style="transition: transform {d(DUR.expand)}ms {EASE_CSS.expand}"
                aria-hidden="true"
              >
                <Icon name="chevron-right" size={16} stroke={2} />
              </span>
              <!-- The trug mark itself, not a generic basket: this drawer *is*
                   the trug. Decorative — the heading text names it — so no
                   `title`, which leaves the svg aria-hidden. -->
              <span class="basket-ico"><Logo size={18} /></span>
              in the basket
              <span class="count">{basket.length}</span>
            </button>
          </h2>
          <button class="clear" type="button" onclick={clearChecked}>Clear</button>
        </div>
        {#if basketOpen}
          <!-- Mounted only while open, never hidden in place: it keeps the
               flip/drag machinery from tracking, measuring and animating rows
               nobody can see. -->
          <!-- `|global`, and it is not optional. As a local transition this one
               is owned by the enclosing `{#if store.checked.length}` block, which
               is not the block being created here — so on the FIRST open Svelte
               built the animation degenerate: duration 0, both keyframes sampled
               at t = 0, `fill: forwards`. The drawer opened, mounted its five
               rows, and then sat pinned at height 0 by a finished animation
               nobody would ever clear. Marking it global attaches it to this
               block, where it belongs, and it plays properly every time. -->
          <div
            class="rows"
            id="basket-region"
            role="region"
            aria-label="In the basket"
            in:unfold|global
            out:fold|global
          >
            {#each basket as item, i (item.id)}
              <div
                class="row-wrap"
                animate:flip={{ duration: d(DUR.flip) }}
                in:arriveRow={{}}
                out:leaveRow={{
                  duration: clearing ? DUR.clear : DUR.exit,
                  easing: clearing ? EASE.clear : EASE.exit,
                  delay: clearing ? staggerDelay(i) : 0,
                }}
              >
                <ItemRow {item} onToggle={toggle} onOpen={open} onSwipeLeft={swipeDelete} pending={store.pendingIds.has(item.id)} />
              </div>
            {/each}
          </div>
        {/if}
      </section>
    {/if}
  {/if}
</div>

{#if swipeUndo}
  <Toast variant="neutral">
    <span>{swipeUndo.text}</span>
    <button class="undo-btn" type="button" onclick={runSwipeUndo}>Undo</button>
  </Toast>
{/if}

{#if editing}
  {#key editing.id}
    <ItemSheet
      item={editing}
      {walkOrder}
      onSave={onUpdate}
      onRemove={deleteWithUndo}
      onClose={() => (editing = null)}
    />
  {/key}
{/if}

<style>
  .list {
    padding: 4px 0 16px;
  }
  /* One uninterrupted base surface for the whole active list, run edge-to-edge:
     no inset card, no side gap, rows and shelf labels reaching both edges of the
     app column. It cancels <main>'s gutter with a negative margin and hands the
     same gutter back down as --shelf-gutter, which the rows and labels re-spend
     as their own inline padding — without that the chip and the shelf label
     would jump 17px left of the add-bar pill the moment the card went away. The
     extra pixel is the border the shelf no longer draws; it used to push the
     content in too, and a pixel of drift between the chip and the add bar is
     exactly the kind of thing you only notice once you've noticed it.

     Radius and shadow leave with the card: a full-bleed band has no side edges
     to round, and its shadow could only ever show as a smear under the top rule.
     Depth is now the mantle→base step of the ladder plus the two hairlines that
     close the band. The clip stays even though there is no longer a radius to
     clip to — it is also what keeps a lifted row and the rows parting around it
     from spilling over the top of the list or down onto the basket.

     `clip`, not `hidden`, and the difference is load-bearing. `overflow: hidden`
     makes this a scroll container, which would capture the aisle bands'
     `position: sticky` and pin them to a box that never scrolls — they would
     simply never stick. `clip` clips without scrolling, so the bands still find
     <main>.

     There was an `overflow-clip-margin: 12px` here, to give back the pixels the
     drag controller's 1.03 lift scale pushes past the edge before the clip
     shaved the lifted row's rounded corners off. The 12px inset ItemRow's
     `.row.lifted` pulls in by (`margin-inline`, and the clearance measurements
     that justify it are stated there) already solves that on its own. The
     margin bought the corners nothing and cost real containment — it let rows
     bleed 12px vertically out of the top and bottom of the list, which is the
     one thing this clip is here for. */
  .shelf {
    --shelf-gutter: calc(var(--app-gutter) + 1px);
    /* The surface a row is lying on, for the few places a row has to paint its
       own opaque copy of it (ItemRow's swiping occluder, the lifted card). The
       basket drawer publishes its own, darker one. */
    --row-ground: var(--ctp-base);
    margin-inline: calc(-1 * var(--app-gutter));
    background: var(--ctp-base);
    border-block: var(--hairline);
    overflow: clip;
  }
  /* Hairline shelf-edge between adjacent aisles (each aisle is an <section>). */
  .shelf > :global(section + section) {
    border-top: var(--hairline);
  }
  /* The basket is a drawer, and it is neither of the two things it has been
     tried as. Not an inset card: by the end of a shop the done pile is most of
     the list, and a container that starts tiny and swells to hold 80% of the
     items reads as a mistake — besides which it spends, on the pile you are
     finished with, the horizontal room the list just won by going edge-to-edge.
     Not another shelf either: an aisle band says "still to get", and a done pile
     that looks exactly like one makes you read it twice on every scroll.

     So: full-bleed like the list, but shut. A generous gap and a full-width rule
     put real distance between the last aisle and the drawer, and what is behind
     the door sits on a recessed ground rather than in a box. No accent anywhere
     in here — accent means "active, needs doing", and spending it on the done
     pile inverts the one signal the list has.

     It keeps publishing --shelf-gutter: its rows are as full-bleed as the
     shelf's, cancel the same column gutter, and have to re-spend it to keep the
     chip line the aisle rows above sit on. */
  .checked {
    --shelf-gutter: calc(var(--app-gutter) + 1px);
    margin-inline: calc(-1 * var(--app-gutter));
    /* Deliberately much larger than the gap between aisles (which is none —
       they share a hairline). This one is the seam between the two halves of
       the list, and it has to be visible without a border to draw it. */
    margin-top: 28px;
    border-top: var(--hairline);
  }
  /* The drawer handle. Same type as a shelf label so it still belongs to the
     list, but no --band-fill: an aisle band's tint is what makes it a band, and
     this is a door. The chevron leads, because it is the thing that says the
     header does something. */
  .drawer-head {
    display: flex;
    align-items: center;
    gap: 4px;
    /* Same gutter trick as the aisle band: a transparent inline border rather
       than padding, so the two buttons still reach the true edges of the
       column and the whole strip stays tappable end to end. */
    border-inline: var(--shelf-gutter, 0px) solid transparent;
  }
  .drawer-head h2 {
    flex: 1 1 auto;
    min-width: 0;
    margin: 0;
    font: inherit;
  }
  .disclosure {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    /* 44px is the one-handed-tap floor the rows hold to as well; this header is
       fixed rather than density-driven because there is exactly one of it, so it
       costs the list no rows either way. */
    min-height: 44px;
    padding: 4px;
    background: none;
    border: none;
    font-family: var(--font-display);
    font-size: var(--aisle-head-size);
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    /* subtext1 clears 4.5:1 on the column's mantle in every flavour. */
    color: var(--ctp-subtext1);
    cursor: pointer;
    transition: color 120ms ease;
  }
  .disclosure:hover {
    color: var(--ctp-text);
  }
  /* subtext0, not the overlay1 the settings disclosures use: this chevron is the
     only thing saying the header opens, and overlay1 on mantle is 2.63:1 in
     Latte — under the 3:1 floor for a control you have to be able to see.
     subtext0 puts it at 4.06:1 there and higher everywhere else. */
  .chev {
    display: inline-flex;
    color: var(--ctp-subtext0);
  }
  .chev.open {
    transform: rotate(90deg);
  }
  /* The mark strokes with `currentColor`. It inherits the label's colour rather
     than taking the accent the way the app header's mark does: the accent is
     the list's "still to get" signal and the basket is the opposite of that. */
  .basket-ico {
    display: inline-flex;
  }
  /* `.count` — how many are already in — is styled in app.css, shared with the
     aisle label's count. The display face comes down from the button. */
  /* Emptying the pile without opening it. A separate control from the
     disclosure, with the flex gap and its own padding between them so there is
     never a doubt about which one a thumb landed on. */
  .clear {
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    min-height: 44px;
    padding: 2px 8px;
    background: none;
    border: none;
    border-radius: var(--radius);
    color: var(--ctp-subtext1);
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: color 120ms ease;
  }
  .clear:hover {
    color: var(--ctp-text);
  }
  /* Behind the door: one rung DOWN the ladder from the column's mantle, so the
     pile reads as recessed into the page rather than raised onto a card. No
     border and no radius — the tone step and the header's rule are the edges. */
  .rows {
    display: flex;
    flex-direction: column;
    --row-ground: var(--ctp-crust);
    background: var(--ctp-crust);
    border-block: var(--hairline);
  }
  .rows > :global(.row-wrap + .row-wrap) {
    border-top: var(--hairline);
  }
  .undo-btn {
    flex: 0 0 auto;
    background: none;
    border: none;
    color: var(--accent);
    font-weight: 600;
    font-size: 15px;
    cursor: pointer;
  }
</style>
