<script lang="ts">
  import { Spring } from 'svelte/motion';
  import { flip } from 'svelte/animate';
  import { untrack, onDestroy, tick } from 'svelte';
  import type { CatalogEntry } from '../lib/types';
  import { ApiError } from '../lib/api';
  import { d, DUR, PRESS_SPRING, PRESS_DIP, settleFlip, reducedMotion } from '../lib/motion';
  import Toast from './Toast.svelte';

  let {
    top,
    activeNames = new Set<string>(),
    onPick,
    onForget,
    onError,
    revision = 0,
  }: {
    /** Fetches the most-added catalogue entries (typically `api.top`). */
    top: (n: number) => Promise<CatalogEntry[]>;
    /** Normalised names already on the list, hidden from the grid. */
    activeNames?: Set<string>;
    onPick: (displayName: string) => void;
    /** Removes a shortcut from the catalogue for good (`api.forget`). Rejects
     *  if the server could not be told.
     *
     *  `keepalive` is passed only on the unload path (see `commit`), where the
     *  document is going away underneath the request: without it the browser
     *  cancels the fetch along with the page and the forget is lost with no
     *  error and no record. It rides through to `fetch`'s own option of the
     *  same name — a caller that does not offer it simply ignores the second
     *  argument, which costs only the hardening, not the flush. */
    onForget: (nameNorm: string, opts?: { keepalive?: boolean }) => Promise<void>;
    /** Where a failed forget is announced. Deliberately the SHELL's error
     *  channel rather than a toast of the tray's own: the two moments a commit
     *  is most likely to fail are also the two moments the tray cannot speak.
     *  It has just been unmounted (typing one character in the add bar does
     *  it), or its one toast slot is held by the undo notice of the NEXT
     *  forget for a full five seconds — and tidying two bad transcriptions in
     *  a row is the likeliest way to use this feature at all. */
    onError: (message: string) => void;
    /** Bumped by the shell whenever something may have changed the catalogue —
     *  an add or a delete, here or on another device. Each new value refetches.
     *  The tray holds no catalogue state of its own to patch, and the ranking it
     *  is fed (frecency over times_added/last_added) is not something the client
     *  can recompute, so a refetch is the only honest way to catch up. */
    revision?: number;
  } = $props();

  let entries = $state<CatalogEntry[]>([]);

  /* How many forgets have been confirmed by the server so far. Deliberately
     plain rather than `$state`: the fetch effect reads it, and a reactive read
     would make every commit schedule a refetch of its own.

     It exists to date a response. A refetch issued at t≈4.9s asked a server
     that still had the row; the DELETE lands at t=5.0s and the answer arrives
     after it, reassigns `entries` wholesale, and re-offers a shortcut the
     server has already deleted — tapping which re-adds the item AND re-creates
     the catalogue row, undoing the forget permanently. It self-heals about a
     second later off this device's own `catalog_forgotten` echo, which is
     exactly the window the shopper is looking at the tray.

     A response whose fetch predates a successful commit is therefore dropped
     whole. The alternative — a tombstone set of committed names filtered out of
     every response — keeps more of a raced answer, but it needs a rule for when
     a tombstone retires, and it gets the re-add wrong: forget a name, add it
     back, and the tombstone hides the row the add just re-created. Dropping the
     response loses nothing visible instead, because `commit` has already
     applied the delete to `entries` locally, and the `catalog_forgotten` echo
     brings a clean answer within the second. */
  let commits = 0;

  $effect(() => {
    void revision; // read as a dependency: a new value means "fetch again"
    let cancelled = false;
    const issuedAt = commits;
    // Deliberate over-fetch: `activeNames` filters client-side, so by the end of
    // a big shop twenty-odd of these are already on the list and would leave the
    // tray half-empty. Two rows plus an expandable full set has more appetite
    // than the old eight-pill cap did, hence the larger surplus.
    top(48)
      .then((res) => {
        if (cancelled) return;
        // Issued before a forget the server has since confirmed: this answer
        // was written down while the row still existed. See `commits`.
        if (issuedAt !== commits) return;
        entries = res;
      })
      .catch((err) => {
        if (cancelled) return;
        // The tray hides itself when empty, so a failing catalogue endpoint is
        // otherwise indistinguishable from "you have no regulars yet" — and on
        // a cold start it stays blank until something asks for it again.
        // Hiding it is still the right behaviour; being silent about why isn't.
        //
        // What is NOT done here is emptying `entries`. At mount it is empty
        // already, so the hide-when-empty behaviour is unchanged — but a
        // REFETCH that fails (the carpark, mid-shop) would otherwise take a
        // perfectly good tray off the screen. Shortcuts do not go stale in any
        // way that matters; they are just as old as the last good fetch.
        console.error('[trug] could not load frequently-added items', err);
      });
    return () => {
      cancelled = true;
    };
  });

  /* Shortcuts the shopper has said to forget, hidden from the tray while the
     undo window runs. Emptied either way: on undo the pill comes back, on
     commit the entry leaves `entries` in the same breath — so a name that is
     later added again is offered again, rather than being hidden by a stale
     tombstone. */
  let forgetting = $state<Set<string>>(new Set());

  const candidates = $derived(
    entries.filter((e) => !activeNames.has(e.name_norm) && !forgetting.has(e.name_norm)),
  );

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
    //
    // The catch is not decoration: pressing a second pill before this spring
    // has settled calls `press.set(…, { instant: true })`, which REJECTS this
    // deferred. Unhandled, that surfaces as "Uncaught (in promise) Error:
    // Aborted" every time a shopper taps two shortcuts quickly — which is how
    // the tray is used. An interrupted settle is the new press taking over, so
    // there is nothing to report and nothing to clean up: the new press owns
    // `pressed` and the guard below already declines to clear it.
    await press.set(1).catch(() => {});
    if (pressed === key) pressed = null;
  }

  /* --- forgetting a shortcut ------------------------------------------------

     A pill is tap-to-add, so the forget gesture has to live somewhere the tap
     is not: a long press, with the same numbers as the app's other long press
     (lib/drag.svelte.ts, where a held row lifts for a drag). One hold in the
     app, one set of numbers — and 12px of movement means the finger is
     scrolling the list, not holding a pill.

     Touch only. 300ms is an ordinary slow click with a mouse, and losing a
     shortcut to one would be a trap; drag.svelte.ts splits touch from mouse in
     exactly this way. A mouse gets the right-click instead (`contextMenu`
     below) — the conventional "do anything but activate this", and the one
     gesture people try on a chip by instinct. Both of them, and the keyboard,
     land in `forget`: one hide, one undo window, one commit.

     The keyboard path (Delete/Backspace on a focused pill) is not the mouse's
     consolation prize but the accessible route: a right-click is invisible to a
     screen reader, and a keyboard-only shopper has no pointer at all. */
  const HOLD_MS = 300;
  const HOLD_CANCEL_PX = 12;
  /** The undo window, matching ListView's delete / clear-basket undo. */
  const UNDO_MS = 5000;

  let hold: { id: number; x: number; y: number; timer: ReturnType<typeof setTimeout> } | null = null;
  /* Set the instant a hold fires, cleared on the next pointerdown. The pointerup
     that ends the hold is the same pointerup a tap ends with, so without this
     the gesture would forget the shortcut and then add the item it just forgot
     — re-creating the catalogue row on the way. It is deliberately not scoped
     to one pill: the tray re-wraps the moment a pill leaves it, so the click can
     land on whichever pill slid into that spot. */
  let fired = false;
  /* What kind of pointer opened the interaction a contextmenu belongs to.
     Recorded on pointerdown, which precedes the menu on every device that
     raises one — see `contextMenu`. */
  let gesturePointer: string | null = null;
  let forgotten = $state<CatalogEntry | null>(null);
  let forgetTimer: ReturnType<typeof setTimeout> | undefined;
  let undoEl = $state<HTMLButtonElement | null>(null);

  function cancelHold(): void {
    if (!hold) return;
    clearTimeout(hold.timer);
    hold = null;
  }

  function unhide(nameNorm: string): void {
    if (!forgetting.has(nameNorm)) return;
    const next = new Set(forgetting);
    next.delete(nameNorm);
    forgetting = next;
  }

  /**
   * Hide the pill and start the undo window. Nothing is sent yet — see `commit`.
   * A second forget lands the first: one pending forget at a time, the way
   * ListView's swipe-undo holds a single slot.
   */
  function forget(entry: CatalogEntry): void {
    commit();
    forgetting = new Set(forgetting).add(entry.name_norm);
    forgotten = entry;
    forgetTimer = setTimeout(() => commit(), UNDO_MS);
    dismissHint();
  }

  /**
   * Tell the server, once the undo window has closed.
   *
   * The request is deferred rather than sent immediately and reversed on undo,
   * which is the opposite of how the list's own undos work — and deliberately.
   * Reversing this one would mean an endpoint that writes a catalogue row back
   * complete with its times_added and last_added, i.e. letting any client forge
   * the ranking the tray is built on, to buy back five seconds. Deferring costs
   * nothing instead: for those five seconds the pill is already gone from the
   * screen, and an undo has nothing to fail at.
   *
   * The one thing it must not do is quietly evaporate — hence the flushes from
   * `onDestroy` and from the page going away, below.
   *
   * `unloading` says the document itself is on the way out (or being frozen),
   * which is the one case the request needs `keepalive` to outlive.
   */
  function commit(unloading = false): void {
    const entry = forgotten;
    clearTimeout(forgetTimer);
    forgetTimer = undefined;
    forgotten = null;
    if (!entry) return;

    // The option is passed only where it earns its keep: `keepalive` requests
    // are capped and given lower priority by the browser, and every other
    // commit is an ordinary in-page request that wants neither.
    (unloading ? onForget(entry.name_norm, { keepalive: true }) : onForget(entry.name_norm))
      .catch((err) => {
        // Already gone server-side (another device forgot it first) is the
        // outcome we wanted, not a failure.
        if (err instanceof ApiError && err.status === 404) return;
        throw err;
      })
      .then(() => {
        // Gone for good: drop it from the tray's own copy and stop hiding it,
        // so the two never disagree about a name that gets added again later.
        // `commits` dates the deletion, so an older answer still in flight
        // cannot put the pill back — see the fetch effect.
        commits += 1;
        entries = entries.filter((e) => e.name_norm !== entry.name_norm);
        unhide(entry.name_norm);
      })
      .catch((err) => {
        // A shortcut still being offered after you told it to go is exactly the
        // complaint this feature exists to answer, so the pill comes back and
        // says so rather than leaving the shopper to notice next Saturday.
        //
        // Said through the shell, not here: by the time this runs the tray has
        // often been unmounted (the flush this whole deferral exists for) or
        // its toast slot has been taken by the next forget's undo. Both of
        // those are ordinary, and both used to eat the message whole.
        console.error(`[trug] could not forget the shortcut "${entry.display_name}"`, err);
        unhide(entry.name_norm);
        onError(`Couldn't forget “${entry.display_name}”`);
      });
  }

  function undoForget(): void {
    if (!forgotten) return;
    clearTimeout(forgetTimer);
    forgetTimer = undefined;
    unhide(forgotten.name_norm);
    forgotten = null;
  }

  function pointerDown(ev: PointerEvent, entry: CatalogEntry): void {
    pressDown(entry.name_norm);
    fired = false;
    gesturePointer = ev.pointerType;
    cancelHold();
    if (ev.pointerType !== 'touch') return;
    hold = {
      id: ev.pointerId,
      x: ev.clientX,
      y: ev.clientY,
      timer: setTimeout(() => {
        hold = null;
        fired = true;
        forget(entry);
      }, HOLD_MS),
    };
  }

  function holdMove(ev: PointerEvent): void {
    if (!hold || ev.pointerId !== hold.id) return;
    // Moved before the hold fired: the finger is scrolling the list, and the
    // pill it happens to have landed on is not the subject of the gesture.
    if (Math.hypot(ev.clientX - hold.x, ev.clientY - hold.y) > HOLD_CANCEL_PX) cancelHold();
  }

  /**
   * Right-click a pill to forget it — the desktop half of the pair.
   *
   * Nothing is fired back to the caller here: it goes through `forget` like
   * everything else, so the pill hides, the undo toast runs its five seconds,
   * and only then is the server told.
   */
  function contextMenu(ev: MouseEvent, entry: CatalogEntry): void {
    // The browser's own menu is suppressed on a pill either way, whichever
    // gesture raised it: after a touch hold it would land on top of the undo
    // toast that hold has just put up, and here on top of the one this handler
    // is about to. Only on a pill — right-clicking the tray or the heading is
    // the browser's business, not ours.
    ev.preventDefault();
    // A touch long press raises a synthetic contextmenu of its own in some
    // browsers. The hold owns that gesture whether it has already fired or is
    // still counting, so forgetting here as well would take a second shortcut
    // with it — the tray re-wraps the instant a pill leaves, so the second one
    // would be whichever pill slid into the empty slot.
    if (gesturePointer === 'touch') return;
    // Deliberately not setting `fired`: a right-click raises no click event, so
    // there is nothing to swallow, and the flag would eat the next left-click
    // add instead.
    forget(entry);
  }

  function pick(displayName: string): void {
    if (fired) {
      fired = false;
      return;
    }
    onPick(displayName);
  }

  function onKeyDown(ev: KeyboardEvent, entry: CatalogEntry, index: number): void {
    // A key press is never the tail of a touch hold, so it also clears the
    // swallow-the-next-click flag: a hold whose click never arrived (the pill
    // it went down on had already left the DOM) would otherwise eat the Enter
    // that activates the next pill.
    fired = false;
    if (ev.key !== 'Delete' && ev.key !== 'Backspace') return;
    ev.preventDefault(); // Backspace is "go back" where it isn't caught
    forget(entry);
    void focusAfter(index);
  }

  /** Keep the keyboard where it was: the pill that took the forgotten one's
   *  place, else the one before it, else the counter. Landing on <body> would
   *  cost a whole tab journey back for the next one.
   *
   *  Forgetting the LAST candidate takes all three away at once — the tray
   *  block, counter included, is inside a `{#if candidates.length}` — so the
   *  undo button backstops them. It is outside that block by design, it is the
   *  only control still to do with what just happened, and it is where a
   *  screen reader has just been sent anyway. */
  async function focusAfter(index: number): Promise<void> {
    await tick();
    const pills = [...(trayEl?.querySelectorAll<HTMLElement>('.pill:not(.more):not(.clipped)') ?? [])];
    (pills[index] ?? pills[index - 1] ?? moreEl ?? undoEl)?.focus();
  }

  /* The discovery problem, and where the hint is allowed to live.
     Junk in the tray is invisible until you see it, and an invisible gesture is
     no fix at all — but the tray's whole job is being quick to scan, so an "×"
     on every pill (which is also a mis-tap away from the add it sits on) is not
     the price to pay. The hint goes on the heading instead: one line of chrome
     you already skip, naming only the gesture this device actually has, and it
     retires itself for good the first time it is acted on. */
  const HINT_KEY = 'trug_forget_hint_used';

  function hintUsed(): boolean {
    try {
      return localStorage.getItem(HINT_KEY) === '1';
    } catch {
      // Private browsing / blocked storage: show the hint. Worst case it is
      // shown to someone who already knows, which is the harmless direction.
      return false;
    }
  }

  let showHint = $state(!hintUsed());

  function dismissHint(): void {
    showHint = false;
    try {
      localStorage.setItem(HINT_KEY, '1');
    } catch {
      // Nothing to do: the hint simply comes back next session.
    }
  }

  /** Touch devices get the hold, everything else the key. Read once — a device
   *  does not grow a touchscreen mid-session. */
  const coarse =
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia('(pointer: coarse)').matches
      : false;

  /* The page going away, which `onDestroy` does not cover and the five-second
     timer cannot survive.

     onDestroy fires for an unmount inside a living page — typing in the add
     bar — and for nothing else: a closing tab, a killed PWA and iOS freezing a
     backgrounded page all take the component without running it. A frozen page
     does not run the timer either, so pocketing the phone inside the undo
     window meant the request was simply never made: no error, no retry, no
     record, and the junk back next Saturday.

     Both events, because neither alone is enough. `pagehide` is the reliable
     one for a real navigation or tab close; `visibilitychange` → hidden is the
     one iOS actually delivers before it freezes a backgrounded page, and is
     also the last event a tab gets when the OS kills it outright. Committing
     twice is not a hazard — `commit` clears `forgotten` before it fires, so
     the second call has nothing to do. */
  $effect(() => {
    if (typeof window === 'undefined') return;
    const flush = () => commit(true);
    const onHidden = () => {
      if (document.visibilityState === 'hidden') flush();
    };
    window.addEventListener('pagehide', flush);
    document.addEventListener('visibilitychange', onHidden);
    return () => {
      window.removeEventListener('pagehide', flush);
      document.removeEventListener('visibilitychange', onHidden);
    };
  });

  onDestroy(() => {
    cancelHold();
    // Typing in the add bar unmounts the tray. A pending forget dying with it
    // would quietly drop a change the shopper has already watched happen, and
    // the junk would be back next time they looked. Not `keepalive`: the page
    // is alive and well, it is only this component that is going.
    commit();
  });
</script>

{#if candidates.length}
  <h2 class="tray-head">
    Frequently added
    {#if showHint}
      <!-- The gesture this device actually has, and only that one. A mouse
           cannot hold (300ms is a slow click) and a touchscreen has no right
           button, so naming both would spend half the line on something the
           reader cannot do. Delete still works on a pointer device and is
           still named in the pill's own description — but right-click is the
           one to teach here: it is what a hand reaches for, and unlike Delete
           it needs no pill focused first, which on this tray costs an add. -->
      <span class="hint"
        >{coarse ? 'hold a shortcut to forget it' : 'right-click a shortcut to forget it'}</span
      >
    {/if}
  </h2>
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
           after it close up under this flip.

           `title` is the pill's accessible description, so on a pointer device
           the KEY comes first: a right-click is something a screen reader
           cannot report and a keyboard-only shopper cannot make, and it must
           not push the route they do have to the back of the sentence. Both
           are named there, because a mouse user hovering a pill is the one
           person who may never have read the hint on the heading. -->
      <button
        class="pill"
        class:clipped={!showAll && i >= cap}
        type="button"
        animate:settleFlip={{ duration: d(DUR.flip), maxTravel: PILL_MAX_TRAVEL }}
        style={pressed === e.name_norm ? `transform: scale(${press.current})` : ''}
        title={coarse
          ? 'Hold to forget this shortcut'
          : 'Press Delete, or right-click, to forget this shortcut'}
        aria-keyshortcuts="Delete"
        onpointerdown={(ev) => pointerDown(ev, e)}
        onpointermove={holdMove}
        onpointerup={() => {
          cancelHold();
          pressUp();
        }}
        onpointerleave={() => {
          cancelHold();
          pressUp();
        }}
        onpointercancel={() => {
          cancelHold();
          pressUp();
        }}
        oncontextmenu={(ev) => contextMenu(ev, e)}
        onkeydown={(ev) => onKeyDown(ev, e, i)}
        onclick={() => pick(e.display_name)}
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

<!-- Outside the tray's own `{#if}` on purpose: forgetting the last shortcut
     empties the tray and hides it, and the way back would go with it. Toast is
     role="status", so this is also what announces the forget to a screen reader
     — a pill simply vanishing announces nothing at all.
     A FAILED forget is not shown here; it goes to the shell's error toast, for
     the reasons given on the `onError` prop. -->
{#if forgotten}
  <Toast variant="neutral">
    <span>Forgot “{forgotten.display_name}”</span>
    <button class="undo-btn" type="button" bind:this={undoEl} onclick={undoForget}>Undo</button>
  </Toast>
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
  /* Quieter than the heading it trails, and lower-case against the heading's
     caps so it reads as an aside rather than a second label. It is the only
     thing on screen that teaches the forget gesture, and it is gone for good
     once used — see `dismissHint`. */
  .hint {
    margin-left: 8px;
    text-transform: none;
    letter-spacing: 0;
    font-weight: 400;
    color: var(--ctp-overlay0);
  }
  /* Matches ListView's undo affordance, which is the only other place in the
     app that offers one. */
  .undo-btn {
    flex: 0 0 auto;
    background: none;
    border: none;
    color: var(--accent);
    font-weight: 600;
    font-size: 15px;
    cursor: pointer;
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
    /* A long press on a button is also the OS gesture for "select this text" /
       "show me a callout". Both would land on top of the undo toast at exactly
       the moment the forget fires. */
    user-select: none;
    -webkit-user-select: none;
    -webkit-touch-callout: none;
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
