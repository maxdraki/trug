import { render, fireEvent } from '@testing-library/svelte';
import { tick } from 'svelte';
import { describe, it, expect, vi, afterEach } from 'vitest';
import ListView from './ListView.svelte';
import Logo from '../lib/Logo.svelte';
import { DUR, staggerDelay } from '../lib/motion';
import { movingStore } from '../lib/testing/movingStore.svelte';
import type { DragController } from '../lib/drag.svelte';
import type { Store } from '../lib/store.svelte';
import type { Item } from '../lib/types';

function item(partial: Partial<Item> & { id: string; name: string }): Item {
  return {
    note: null,
    icon: null,
    category: 'Drinks',
    status: 'checked',
    source: null,
    added_by: null,
    created_at: new Date().toISOString(),
    checked_at: new Date().toISOString(),
    sort_key: 0,
    ...partial,
  };
}

/** A plain object implementing the Store read surface the view touches. */
function store(checked: Item[]): Store {
  return {
    groups: [],
    checked,
    online: true,
    pendingIds: new Set<string>(),
    snapshotAt: null,
    refreshed: true,
    refresh: vi.fn(),
    retry: vi.fn(),
    add: vi.fn(),
    toggle: vi.fn(),
    remove: vi.fn(),
    reorder: vi.fn(),
    clearChecked: vi.fn(),
    applyEvent: vi.fn(),
    dispose: vi.fn(),
  } as unknown as Store;
}

function renderList(checked: Item[]) {
  const s = store(checked);
  return {
    ...render(ListView, { props: { store: s, walkOrder: ['Drinks'], onUpdate: vi.fn() } }),
    store: s,
  };
}

/** The `d` of every path in an svg — what it draws, independent of how it is sized. */
function shapes(svg: Element): (string | null)[] {
  return [...svg.querySelectorAll('path')].map((p) => p.getAttribute('d'));
}

describe('ListView basket header', () => {
  it('marks the section with the trug mark itself, not a stock basket glyph', () => {
    // The drawer IS the trug, so the mark on it has to be the app's own. The
    // icon set has a perfectly good `basket`, and reaching for it here would be
    // silently wrong rather than broken — which is what this has to catch.
    // Compared against a real Logo render rather than to a fixed viewBox: the
    // mark is free to be redrawn or re-gridded and this follows it there, while
    // still failing the moment the header stops wearing it.
    const { container } = renderList([item({ id: 'beer', name: 'Beer' })]);
    const mark = container.querySelector('.basket-ico svg');
    expect(mark).not.toBeNull();

    const reference = render(Logo, { props: { size: 18 } }).container.querySelector('svg')!;
    expect(shapes(reference).length).toBeGreaterThan(3);
    expect(shapes(mark!)).toEqual(shapes(reference));
  });

  it('keeps the mark decorative and on the label’s optical line', () => {
    const { container, getByText } = renderList([item({ id: 'beer', name: 'Beer' })]);

    const svg = container.querySelector('.basket-ico svg')!;
    expect(svg.getAttribute('aria-hidden')).toBe('true');
    expect(svg.getAttribute('role')).toBeNull();
    expect(svg.getAttribute('aria-label')).toBeNull();
    // Sized into the label's run of glyphs — between the 16px chevron beside it
    // and the 20px chip on the rows below — and never stretched. The mark's own
    // 24px default would tower over the header it belongs to.
    const width = Number(svg.getAttribute('width'));
    const height = Number(svg.getAttribute('height'));
    expect(width).toBe(height);
    expect(width).toBeGreaterThanOrEqual(16);
    expect(width).toBeLessThanOrEqual(20);
    // The adjacent text still names the section, and the count is untouched.
    expect(getByText('in the basket')).toBeTruthy();
    expect(container.querySelector('.count')!.textContent).toBe('1');
  });

  it('empties the pile from the closed header, and offers it back', async () => {
    // The basket has been restyled repeatedly (inset card -> full-bleed band ->
    // drawer). The name, the mark and the count are covered above; Clear is the
    // one thing the section is FOR, and it has to keep working from the closed
    // header — emptying the pile must not cost you an open and a close. Pressing
    // it, rather than merely finding it, is the whole difference between this
    // and a test a deleted `onclick` would sail straight through.
    const {
      getByRole,
      getByText,
      store: s,
    } = renderList([item({ id: 'beer', name: 'Beer' }), item({ id: 'soda', name: 'Soda' })]);
    expect(getByRole('button', { name: /in the basket/i }).getAttribute('aria-expanded')).toBe(
      'false',
    );

    await fireEvent.click(getByRole('button', { name: 'Clear' }));

    expect(s.clearChecked).toHaveBeenCalledTimes(1);
    // Two rows go in one press with no confirmation step, so the way back is the
    // only thing standing between a mis-tap and a re-typed list.
    expect(getByText('Cleared 2 items')).toBeTruthy();
    expect(getByRole('button', { name: 'Undo' })).toBeTruthy();
  });
});

/**
 * A manual animation-frame queue. The drawer's reveal chases the unfold frame by
 * frame, so a test has to be able to say "a frame passed" — sleeping in real
 * time beside jsdom's own rAF is a race, and it is the machine, not the test,
 * that decides how many frames the assertion is looking at.
 */
function useFrames() {
  const queue: FrameRequestCallback[] = [];
  const real = window.requestAnimationFrame;
  window.requestAnimationFrame = ((cb: FrameRequestCallback) => {
    queue.push(cb);
    return queue.length;
  }) as typeof window.requestAnimationFrame;
  return {
    /** Run the frames queued so far. A callback that queues another is not re-run. */
    flush() {
      for (const cb of queue.splice(0)) cb(performance.now());
    },
    restore() {
      window.requestAnimationFrame = real;
    },
  };
}

describe('ListView basket drawer', () => {
  it('starts closed and mounts nothing behind the door', () => {
    // Not `display: none`: a hidden-but-mounted row still registers with the
    // crossfade and still measures, as a zero-size box at the origin, which
    // throws unrelated rows in from the corner of the viewport.
    const { queryByText, queryByRole } = renderList([item({ id: 'beer', name: 'Beer' })]);

    expect(queryByRole('button', { name: /in the basket/i })!.getAttribute('aria-expanded')).toBe(
      'false',
    );
    expect(queryByText('Beer')).toBeNull();
    expect(queryByRole('region', { name: 'In the basket' })).toBeNull();
  });

  it('reveals the pile on disclosure and puts it away again', async () => {
    const { getByRole, queryByText } = renderList([item({ id: 'beer', name: 'Beer' })]);
    const disclosure = getByRole('button', { name: /in the basket/i });

    await fireEvent.click(disclosure);
    expect(disclosure.getAttribute('aria-expanded')).toBe('true');
    expect(queryByText('Beer')).not.toBeNull();

    await fireEvent.click(disclosure);
    expect(disclosure.getAttribute('aria-expanded')).toBe('false');
  });

  it('never points the disclosure at a region that is not in the document', () => {
    // The basket region is mounted only while the drawer is open, so a static
    // `aria-controls` on the handle dangled in the state every page load starts
    // in — a screen reader following it found nothing. `aria-expanded` alone
    // (asserted above) is valid and carries the state that matters.
    const { getByRole } = renderList([item({ id: 'beer', name: 'Beer' })]);
    const disclosure = getByRole('button', { name: /in the basket/i });

    const controls = disclosure.getAttribute('aria-controls');
    if (controls !== null) expect(document.getElementById(controls)).not.toBeNull();
  });

  it('brings the drawer into view on open and leaves the viewport alone on close', async () => {
    // Opening grows the drawer downwards, off the bottom of the screen — past
    // the pile you just asked to see. Closing must not move the viewport at all:
    // there is nothing to reveal and yanking the list is worse than doing
    // nothing. (jsdom has no layout, so this pins down who gets asked to scroll
    // and how — where it actually lands was measured in a browser.)
    const calls: { el: Element; opts: unknown }[] = [];
    const frames = useFrames();
    // The component walks up to the scroll container, so it needs a real one.
    const main = document.createElement('main');
    document.body.appendChild(main);
    try {
      // jsdom implements no layout and so no scrollIntoView at all; define one.
      // Patched INSIDE the try: done before it, a throw from the render below
      // skipped the `finally` and leaked this stub into every later suite.
      Object.defineProperty(Element.prototype, 'scrollIntoView', {
        configurable: true,
        writable: true,
        value: function (this: Element, opts: unknown) {
          calls.push({ el: this, opts });
        },
      });
      const { getByRole, container } = renderList([item({ id: 'beer', name: 'Beer' })]);
      main.appendChild(container);
      const disclosure = getByRole('button', { name: /in the basket/i });

      await fireEvent.click(disclosure);
      await tick();
      frames.flush();
      expect(calls.length).toBeGreaterThan(0);
      // The whole drawer, header included — aiming at the contents alone would
      // scroll the header off the top in the taller-than-the-screen case.
      expect(calls[0].el).toBe(container.querySelector('.checked'));
      expect(calls[0].opts).toMatchObject({ block: 'nearest' });

      const onOpen = calls.length;
      await fireEvent.click(disclosure);
      await tick();
      // Two frames' worth: the chase re-queues itself, so one flush alone could
      // not tell a loop that stopped from a loop that had simply not run yet.
      frames.flush();
      frames.flush();
      expect(calls).toHaveLength(onOpen);
    } finally {
      frames.restore();
      main.remove();
      delete (Element.prototype as unknown as Record<string, unknown>).scrollIntoView;
    }
  });
});

// --- the check-off hold ------------------------------------------------------
//
// Tapping a row used to destroy its ItemRow on the same tick, so the strike-
// through and the chip squash — the only feedback at the point of contact —
// never played. The row is now held on its shelf for the length of the strike.
// What jsdom can pin down is the state machine around that: who is rendered,
// when the store is told, and that nothing stacks or leaks. It runs no
// transitions and lays nothing out, so the strike actually drawing and the chip
// actually squashing are browser measurements, not assertions here.
//
// The fake store is `$state`-backed (lib/testing/movingStore.svelte.ts) and that
// is load-bearing rather than tidy: against a non-reactive one the view renders
// once and never again, so every "the row is STILL on the shelf" assertion below
// is satisfied by a frozen DOM — and passes with the hold deleted outright.

function active(partial: Partial<Item> & { id: string; name: string }): Item {
  return item({ status: 'active', checked_at: null, ...partial });
}

function renderShelf(store: Store, drag?: DragController) {
  return render(ListView, { props: { store, walkOrder: ['Drinks'], onUpdate: vi.fn(), drag } });
}

/** Install a matchMedia stub whose reduce query answers `reduce`. */
function mockReducedMotion(reduce: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (q: string) => ({ matches: reduce && /reduce/.test(q), media: q }) as MediaQueryList,
  });
}

/**
 * Make outros actually finish.
 *
 * The shared setup's `element.animate` never calls `onfinish`, so a node playing
 * an outro sits in the DOM for ever and "it has gone" is unassertable — which is
 * how a broken hold can look exactly like a working one. Landing animations on
 * the next timer turn, as a browser lands them at the end of the animation, is
 * what lets a test tell "still here because it is held" from "still here because
 * nothing in this environment ever leaves".
 */
function landAnimations() {
  const original = Object.getOwnPropertyDescriptor(Element.prototype, 'animate');
  Object.defineProperty(Element.prototype, 'animate', {
    configurable: true,
    writable: true,
    value: () =>
      ({
        cancel() {},
        finish() {},
        play() {},
        pause() {},
        commitStyles() {},
        addEventListener() {},
        removeEventListener() {},
        set onfinish(finish: () => void) {
          setTimeout(finish, 0);
        },
        oncancel: null,
        currentTime: 0,
        startTime: 0,
        playState: 'finished',
        finished: Promise.resolve(),
      }) as unknown as Animation,
  });
  return () => {
    if (original) Object.defineProperty(Element.prototype, 'animate', original);
  };
}

/** Run the timers a leaving node needs: its own outro, then the landing of it. */
async function settle(ms: number) {
  vi.advanceTimersByTime(ms);
  await tick();
  vi.advanceTimersByTime(1);
  await tick();
  await tick();
}

describe('ListView check-off hold', () => {
  let restoreAnimations: (() => void) | null = null;

  afterEach(() => {
    restoreAnimations?.();
    restoreAnimations = null;
    vi.useRealTimers();
    // @ts-expect-error - drop the stub so unrelated suites see the setup default
    delete window.matchMedia;
  });

  it('tells the store at once, and keeps the row on the shelf, struck through', async () => {
    vi.useFakeTimers();
    const { store, toggle, state } = movingStore([
      { category: 'Drinks', items: [active({ id: 'beer', name: 'Beer' })] },
    ]);
    const { container, getByRole } = renderShelf(store);

    await fireEvent.click(getByRole('button', { name: 'Beer' }));

    // Committed on the tap: nothing about the hold delays the mutation.
    expect(toggle).toHaveBeenCalledTimes(1);
    expect(state.checked.map((i) => i.id)).toEqual(['beer']);

    // ...and still rendered where it stood, wearing the checked state that the
    // strike-through and the squash are keyed to.
    const row = container.querySelector('.shelf .row');
    expect(row).not.toBeNull();
    expect(row!.classList.contains('checked')).toBe(true);
    expect(row!.querySelector('.name')!.textContent!.trim()).toBe('Beer');
  });

  it('keeps the aisle alive when the held row was its last, then takes it with it', async () => {
    // Svelte skips a local transition whose enclosing block is destroyed, so an
    // aisle emptied by its final check-off would take the row with it in one
    // frame — the original bug in a different hat.
    restoreAnimations = landAnimations();
    vi.useFakeTimers();
    const { store, state } = movingStore([
      { category: 'Drinks', items: [active({ id: 'beer', name: 'Beer' })] },
    ]);
    const { container, getByRole } = renderShelf(store);

    await fireEvent.click(getByRole('button', { name: 'Beer' }));

    // The store has emptied the aisle; the view stands it back up around the
    // held row, with the row inside it wearing its check.
    expect(state.groups).toHaveLength(0);
    const aisle = container.querySelector('.shelf .aisle');
    expect(aisle).not.toBeNull();
    expect(aisle!.querySelector('.row')!.classList.contains('checked')).toBe(true);
    expect(container.querySelector('.shelf h2')!.textContent).toContain('Drinks');

    // And standing it back up is ALL it does: the aisle goes when the hold ends.
    // One that outlived its last row would be a shelf label over nothing.
    await settle(DUR.check);
    await settle(DUR.collapse + DUR.exit);
    expect(container.querySelector('.shelf .aisle')).toBeNull();
  });

  it('does not dim a held row as pending, and dims it again in the basket', async () => {
    // The queued-op fade would run straight over the confirmation the hold
    // exists to show. The row wears it again once it lands in the basket, where
    // there is nothing left for it to obscure.
    vi.useFakeTimers();
    const { store } = movingStore([
      { category: 'Drinks', items: [active({ id: 'beer', name: 'Beer' })] },
    ]);
    const { container, getByRole } = renderShelf(store);

    await fireEvent.click(getByRole('button', { name: 'Beer' }));

    expect(store.pendingIds.has('beer')).toBe(true);
    const held = container.querySelector('.shelf .row')!;
    expect(held.classList.contains('checked')).toBe(true);
    expect(held.classList.contains('pending')).toBe(false);

    vi.advanceTimersByTime(DUR.check);
    await tick();
    await fireEvent.click(getByRole('button', { name: /in the basket/i }));

    expect(store.pendingIds.has('beer')).toBe(true);
    // Addressed through the basket's own region id: the vacated shelf row is
    // still in the document playing its collapse, and it lives in a `.rows` of
    // its own.
    expect(
      container.querySelector('#basket-region .row')!.classList.contains('pending'),
    ).toBe(true);
  });

  it('shows a held row in one place only — not on the shelf and in the basket at once', async () => {
    restoreAnimations = landAnimations();
    vi.useFakeTimers();
    const { store } = movingStore([
      { category: 'Drinks', items: [active({ id: 'beer', name: 'Beer' })] },
    ]);
    const { container, getByRole, queryByRole } = renderShelf(store);

    await fireEvent.click(getByRole('button', { name: 'Beer' }));

    // The basket is still empty as far as the view is concerned: the row has not
    // left the shelf yet, so it has not arrived anywhere either.
    expect(queryByRole('button', { name: /in the basket/i })).toBeNull();
    expect(container.querySelectorAll('.row')).toHaveLength(1);
    expect(container.querySelector('.shelf .row')).not.toBeNull();

    // And once it has gone, the basket owns it and there is still only one of
    // it: a hold outliving the hand-over would render the same item twice.
    await settle(DUR.check);
    await settle(DUR.collapse + DUR.exit);
    await fireEvent.click(getByRole('button', { name: /in the basket/i }));
    expect(container.querySelectorAll('.row')).toHaveLength(1);
    expect(container.querySelector('#basket-region .row')).not.toBeNull();
    expect(container.querySelector('.shelf .row')).toBeNull();
  });

  it('lets the row go once the strike has drawn', async () => {
    vi.useFakeTimers();
    const { store } = movingStore([
      { category: 'Drinks', items: [active({ id: 'beer', name: 'Beer' })] },
    ]);
    const { container, getByRole } = renderShelf(store);

    await fireEvent.click(getByRole('button', { name: 'Beer' }));
    expect(container.querySelector('.shelf')).not.toBeNull();

    vi.advanceTimersByTime(DUR.check);
    await tick();

    // Handed over: the basket now owns it, and the shelf's own outro takes it
    // from here (a 200ms collapse jsdom does not run).
    expect(getByRole('button', { name: /in the basket/i })).toBeTruthy();
    expect(container.querySelector('.drawer-head .count')!.textContent).toBe('1');
  });

  it('holds nothing under reduced motion — the row simply leaves', async () => {
    // The hold routes through d(), the same gate as every other travel
    // duration, so it collapses to zero with the rest of them. Which has to mean
    // the row LEAVES THE SHELF on the tap, not merely that no timer was
    // scheduled: a hold that skipped its timer and kept rendering the row would
    // strand it there for the rest of the session.
    mockReducedMotion(true);
    vi.useFakeTimers();
    const { store, toggle, state } = movingStore([
      { category: 'Drinks', items: [active({ id: 'beer', name: 'Beer' })] },
    ]);
    const { container, getByRole } = renderShelf(store);

    await fireEvent.click(getByRole('button', { name: 'Beer' }));

    expect(toggle).toHaveBeenCalledTimes(1);
    expect(state.checked.map((i) => i.id)).toEqual(['beer']);
    expect(container.querySelector('.shelf .row')).toBeNull();
    expect(container.querySelector('.shelf .aisle')).toBeNull();
    expect(getByRole('button', { name: /in the basket/i })).toBeTruthy();
    // No hold was scheduled — no delay to sit through. The one timer left is
    // the undo toast's five seconds, which is function rather than motion and
    // so is deliberately NOT gated on reduced motion; a second timer here would
    // mean a hold had been armed after all.
    expect(vi.getTimerCount()).toBe(1);
    expect(getByRole('button', { name: 'Undo' })).toBeTruthy();
  });

  it('checks off several rows in a row without stacking or stranding one', async () => {
    // The real gesture: a shopper going down the aisle faster than one hold.
    vi.useFakeTimers();
    const { store, toggle } = movingStore([
      {
        category: 'Drinks',
        items: [active({ id: 'beer', name: 'Beer' }), active({ id: 'soda', name: 'Soda' })],
      },
    ]);
    const { container, getByRole } = renderShelf(store);

    await fireEvent.click(getByRole('button', { name: 'Beer' }));
    vi.advanceTimersByTime(50);
    await fireEvent.click(getByRole('button', { name: 'Soda' }));

    // Both committed on their own tap; both still on the shelf, both struck.
    expect(toggle).toHaveBeenCalledTimes(2);
    const held = [...container.querySelectorAll('.shelf .row')];
    expect(held).toHaveLength(2);
    expect(held.every((r) => r.classList.contains('checked'))).toBe(true);

    // Each hold runs its own clock — the first ends 50ms before the second, and
    // each row reaches the basket as its own hold ends. (The vacated shelf row
    // is a 200ms collapse jsdom does not run, so the basket count is what says
    // the hand-over happened.)
    vi.advanceTimersByTime(DUR.check - 50);
    await tick();
    expect(container.querySelector('.drawer-head .count')!.textContent).toBe('1');

    vi.advanceTimersByTime(50);
    await tick();
    expect(container.querySelector('.drawer-head .count')!.textContent).toBe('2');
    // Both holds have run out. The one timer left is the undo toast's, and
    // there is exactly ONE of it for two check-offs — each arming retires the
    // last, so a shopper going down the aisle does not accumulate timers.
    expect(getByRole('button', { name: 'Undo' })).toBeTruthy();
    expect(vi.getTimerCount()).toBe(1);
  });

  it('un-checks on a second tap inside the hold window, without a stale release', async () => {
    // Outros land here, so the row the second tap lands on is genuinely still
    // on the shelf rather than a node jsdom simply never got around to removing.
    restoreAnimations = landAnimations();
    vi.useFakeTimers();
    const { store, toggle, state } = movingStore([
      { category: 'Drinks', items: [active({ id: 'beer', name: 'Beer' })] },
    ]);
    const { container, getByRole } = renderShelf(store);

    await fireEvent.click(getByRole('button', { name: 'Beer' }));
    vi.advanceTimersByTime(60);
    // Reachable at all only because the hold is still rendering the row here.
    await fireEvent.click(getByRole('button', { name: 'Beer' }));

    expect(toggle).toHaveBeenCalledTimes(2);
    expect(state.checked).toHaveLength(0);
    // Back to an ordinary active row: the store's answer, not the held copy.
    const row = container.querySelector('.shelf .row')!;
    expect(row.classList.contains('checked')).toBe(false);

    // And the first hold's timer must not fire later and take it away again.
    vi.advanceTimersByTime(DUR.check);
    await tick();
    const after = container.querySelector('.shelf .row');
    expect(after).not.toBeNull();
    expect(after!.classList.contains('checked')).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('leaves no timer running when the view goes away', async () => {
    vi.useFakeTimers();
    const { store } = movingStore([
      { category: 'Drinks', items: [active({ id: 'beer', name: 'Beer' })] },
    ]);
    const { getByRole, unmount } = renderShelf(store);

    await fireEvent.click(getByRole('button', { name: 'Beer' }));
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('takes neither a swipe nor a drag while a row is held', async () => {
    // A gesture on a row that is already leaving cannot mean anything reliable:
    // a swipe-delete landing mid-hold acts on a row the store has already moved,
    // and a drag picks up a copy that is about to stop existing. The row stays
    // tappable — a second tap un-checks it — and takes nothing else.
    vi.useFakeTimers();
    const drag = {
      draggingId: null,
      dropCategory: null,
      target: null,
      originCategory: null,
      settlingId: null,
      ghostId: null,
      gapHeight: 0,
      start: vi.fn(),
    } as unknown as DragController;
    const { store } = movingStore([
      {
        category: 'Drinks',
        items: [active({ id: 'beer', name: 'Beer' }), active({ id: 'soda', name: 'Soda' })],
      },
    ]);
    const { container, getByRole } = renderShelf(store, drag);

    await fireEvent.click(getByRole('button', { name: 'Beer' }));

    const rows = [...container.querySelectorAll('.shelf .row')];
    const named = (name: string) =>
      rows.find((r) => r.querySelector('.name')!.textContent!.trim() === name)!;
    const held = named('Beer');
    const neighbour = named('Soda');

    expect(held.classList.contains('checked')).toBe(true);
    // `.swipeable` is on a row only when it was handed a swipe handler, and
    // `.chip.handle` only when it was handed a drag start.
    expect(held.classList.contains('swipeable')).toBe(false);
    expect(held.querySelector('.chip.handle')).toBeNull();
    // Its untouched neighbour keeps both, so this is the hold talking and not a
    // list that simply never wires either up.
    expect(neighbour.classList.contains('swipeable')).toBe(true);
    expect(neighbour.querySelector('.chip.handle')).not.toBeNull();
  });
});

/** A row can only commit a swipe on distance, and a zero-width row has none. */
function stubRowWidth(px: number) {
  const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth');
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
    configurable: true,
    get: () => px,
  });
  return () => {
    if (original) Object.defineProperty(HTMLElement.prototype, 'offsetWidth', original);
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>).offsetWidth;
  };
}

/** Drag `target` horizontally to `to` and let go: past the axis lock, then commit. */
async function swipe(target: Element, to: number) {
  await fireEvent.pointerDown(target, { pointerId: 1, clientX: 0, clientY: 0 });
  await fireEvent.pointerMove(window, { pointerId: 1, clientX: Math.sign(to) * 20, clientY: 0 });
  await fireEvent.pointerMove(window, { pointerId: 1, clientX: to, clientY: 0 });
  await fireEvent.pointerUp(window, { pointerId: 1, clientX: to, clientY: 0 });
  // The phantom click a browser fires after a pointer gesture. The committing
  // row deliberately swallows exactly one of these (so a swipe is not also a
  // tap), and without it here the swallow would eat the test's next click.
  await fireEvent.click(target);
}

// Tapping a row and swiping it right are the same act with the same
// consequence, so they get the same undo. The gesture decides how the row
// LEAVES — a swipe has a direction and momentum to follow through, a tap has
// neither — but what is offered back afterwards belongs to the outcome, not to
// how it was asked for. A mistap used to cost a hunt through a basket drawer
// that is collapsed by default.
describe('ListView check-off undo', () => {
  let restoreAnimations: (() => void) | null = null;

  afterEach(() => {
    restoreAnimations?.();
    restoreAnimations = null;
    vi.useRealTimers();
  });

  it('offers an undo on a tapped check-off, the same as a swiped one', async () => {
    const { store, state } = movingStore([
      { category: 'Drinks', items: [active({ id: 'beer', name: 'Beer' })] },
    ]);
    const { getByRole } = renderShelf(store);

    await fireEvent.click(getByRole('button', { name: 'Beer' }));
    expect(state.checked.map((i) => i.id)).toEqual(['beer']);

    await fireEvent.click(getByRole('button', { name: 'Undo' }));
    expect(state.checked).toHaveLength(0);
    expect(state.groups.flatMap((g) => g.items).map((i) => i.id)).toEqual(['beer']);
  });

  it('retires the undo when the same row is acted on again', async () => {
    // Check a row off, then un-check it by hand inside the five seconds. The
    // toast still read "In the basket" and its button called the same toggle —
    // which now CHECKS IT OFF a second time. An Undo that does the opposite of
    // undo is worse than no Undo, and taps make this trivially reachable.
    restoreAnimations = landAnimations();
    vi.useFakeTimers();
    const { store, state } = movingStore([
      { category: 'Drinks', items: [active({ id: 'beer', name: 'Beer' })] },
    ]);
    const { getByRole, queryByRole } = renderShelf(store);

    await fireEvent.click(getByRole('button', { name: 'Beer' }));
    expect(getByRole('button', { name: 'Undo' })).toBeTruthy();

    await fireEvent.click(getByRole('button', { name: 'Beer' }));
    expect(state.checked).toHaveLength(0);
    // Landed, not merely asked to go: a toast mid-outro is still in the tree
    // with a live button in it, so asserting absence without running the outro
    // would pass against a toast that had never been retired at all.
    await settle(DUR.toastOut);
    expect(queryByRole('button', { name: 'Undo' })).toBeNull();
  });

  it('offers nothing on an UNCHECK — there is no consequence to undo', async () => {
    // Tapping a row in the basket puts it back on the shelf, which is already
    // the undo. A toast here would be an undo for an undo, and it would sit in
    // the one slot a real one needs.
    const { store } = movingStore([], [item({ id: 'beer', name: 'Beer' })]);
    const { getByRole, queryByRole } = renderShelf(store);

    await fireEvent.click(getByRole('button', { name: /in the basket/i }));
    await fireEvent.click(getByRole('button', { name: 'Beer' }));

    expect(queryByRole('button', { name: 'Undo' })).toBeNull();
  });
});

// There is one toast slot, and three things want it. Two of them cannot be got
// back any other way — a delete takes the item's note with it, and clearing the
// basket takes twelve notes — while a check-off is one tap from undone in the
// basket drawer. So the cheap one never displaces an expensive one, and nothing
// is ever left alive-but-hidden with its own clock running out behind a mask.
describe('ListView undo priority', () => {
  let restoreAnimations: (() => void) | null = null;

  afterEach(() => {
    restoreAnimations?.();
    restoreAnimations = null;
    vi.useRealTimers();
  });

  it('will not let a check-off displace the undo for a cleared basket', async () => {
    // The expensive one. Clearing offers twelve rows back BY NAME AND NOTE —
    // the notes exist nowhere else once the rows are gone. Ticking one more
    // thing off used to replace that notice with "in the basket", while the
    // cleared pile's own five seconds ran out unseen behind it: always masked,
    // always expiring first, and gone with no signal at all.
    const { store } = movingStore(
      [{ category: 'Drinks', items: [active({ id: 'tea', name: 'Tea' })] }],
      [item({ id: 'beer', name: 'Beer' })],
    );
    const { getByRole, getByText } = renderShelf(store);

    await fireEvent.click(getByRole('button', { name: /in the basket/i }));
    await fireEvent.click(getByRole('button', { name: 'Clear' }));
    expect(getByText('Cleared 1 item')).toBeTruthy();

    await fireEvent.click(getByRole('button', { name: 'Tea' }));

    // Still the cleared basket's, and still the one Undo acts on.
    expect(getByText('Cleared 1 item')).toBeTruthy();
    await fireEvent.click(getByRole('button', { name: 'Undo' }));
    expect(store.add).toHaveBeenCalledWith('Beer', undefined);
  });

  it('replaces a check-off undo when the basket is then cleared', async () => {
    // The reverse order, and the reason one slot beats two: the check-off
    // notice used to sit ON TOP of the freshly-armed clear. Its Undo called
    // toggle on a row the clear had already removed — `store.toggle` finds
    // nothing and returns — so the shopper pressed Undo, watched nothing
    // happen, and saw the toast change its own wording.
    restoreAnimations = landAnimations();
    vi.useFakeTimers();
    const { store } = movingStore(
      [{ category: 'Drinks', items: [active({ id: 'tea', name: 'Tea' })] }],
      [item({ id: 'beer', name: 'Beer' })],
    );
    const { getByRole, getByText, queryByText } = renderShelf(store);

    await fireEvent.click(getByRole('button', { name: 'Tea' }));
    expect(getByText('Tea in the basket')).toBeTruthy();

    await fireEvent.click(getByRole('button', { name: /in the basket/i }));
    await fireEvent.click(getByRole('button', { name: 'Clear' }));

    await settle(DUR.toastOut);
    expect(queryByText('Tea in the basket')).toBeNull();
    expect(getByText('Cleared 2 items')).toBeTruthy();
  });

  it('names the row it is offering back', async () => {
    // A bare "In the basket" is the same sentence whichever row it is about.
    // Tick off Milk, Bread and Beer in one breath and the notice never changes
    // — so a shopper correcting the Milk mistap presses Undo and gets BEER
    // back, having been given nothing to notice with. It is also why a screen
    // reader announced the first check-off and then went quiet: a live region
    // whose text does not change does not re-announce.
    const { store } = movingStore([
      {
        category: 'Drinks',
        items: [active({ id: 'milk', name: 'Milk' }), active({ id: 'beer', name: 'Beer' })],
      },
    ]);
    const { getByRole, getByText } = renderShelf(store);

    await fireEvent.click(getByRole('button', { name: 'Milk' }));
    expect(getByText('Milk in the basket')).toBeTruthy();
    await fireEvent.click(getByRole('button', { name: 'Beer' }));
    expect(getByText('Beer in the basket')).toBeTruthy();
  });
});

describe('ListView swipe to the basket', () => {
  let restoreWidth: (() => void) | null = null;
  let restoreAnimations: (() => void) | null = null;

  afterEach(() => {
    restoreWidth?.();
    restoreWidth = null;
    restoreAnimations?.();
    restoreAnimations = null;
    vi.useRealTimers();
  });

  it('holds the row on its shelf for the whole slide, not just the strike', async () => {
    // A tap's confirmation is the strike drawing, so DUR.check is the right
    // window for it. A right-swipe's confirmation is the row finishing the
    // journey the thumb threw it on, which takes DUR.swipeCommit — and held for
    // the shorter one the row is pulled off the shelf with ~40% of its own
    // slide still to run, which is the check-off equivalent of the destroyed
    // ItemRow that hold.svelte.ts was built to stop.
    restoreWidth = stubRowWidth(300);
    restoreAnimations = landAnimations();
    vi.useFakeTimers();
    const { store } = movingStore([
      { category: 'Drinks', items: [active({ id: 'beer', name: 'Beer' })] },
    ]);
    const { container, getByText } = renderShelf(store);

    await swipe(getByText('Beer'), 200);
    expect(container.querySelector('.shelf .row')).not.toBeNull();

    // Past the point a TAP would have let go, with the slide still running.
    await settle(DUR.check + 10);
    expect(container.querySelector('.shelf .row')).not.toBeNull();

    // Past the end of the slide, and the shelf lets it go.
    await settle(DUR.swipeCommit + DUR.exit);
    expect(container.querySelector('.shelf .row')).toBeNull();
  });

  it('undoes a swipe-to-basket inside the hold window, leaving one row', async () => {
    // The undo deliberately routes back through the view's own `toggle` rather
    // than straight to `store.toggle`: pressed inside the hold window it has to
    // let the held copy GO as well as un-check the row, or the same id is keyed
    // twice in one each-block — which is a crash, not a cosmetic glitch.
    restoreWidth = stubRowWidth(300);
    vi.useFakeTimers();
    const { store, toggle } = movingStore([
      { category: 'Drinks', items: [active({ id: 'beer', name: 'Beer' })] },
    ]);
    const { container, getByText, getByRole } = renderShelf(store);

    await swipe(getByText('Beer'), 200);
    // Named, the same as a tap's — the gesture differs, the notice does not.
    expect(getByText('Beer in the basket')).toBeTruthy();
    expect(container.querySelector('.shelf .row')!.classList.contains('checked')).toBe(true);

    vi.advanceTimersByTime(60);
    await fireEvent.click(getByRole('button', { name: 'Undo' }));
    await tick();

    const rows = container.querySelectorAll('.row');
    expect(rows).toHaveLength(1);
    expect(rows[0].classList.contains('checked')).toBe(false);
    // The hold really was let go, rather than merely out-voted by the store's
    // copy: a row that is still held is the one row in the list taking no swipe.
    expect(rows[0].classList.contains('swipeable')).toBe(true);
    // ...and the row is VISIBLE. The each-block is keyed by id, so an undo
    // inside the hold window hands the restored row the very same node the
    // commit parked off-screen. Everything the slide left on it has to have
    // gone, or the row comes back as an empty slab — content translated a full
    // width out under `overflow: hidden`, its tick field still showing, and its
    // toggle button clipped out of reach, with nothing left to re-render it.
    const content = rows[0].querySelector('.swipe-content') as HTMLElement;
    expect(content.style.transform).toBe('');
    expect(content.style.transition).toBe('');
    expect(content.classList.contains('swiping')).toBe(false);
    expect(toggle).toHaveBeenCalledTimes(2);

    // ...and the abandoned hold's timer must not fire later and re-take the row.
    vi.advanceTimersByTime(DUR.check);
    await tick();
    expect(container.querySelectorAll('.row')).toHaveLength(1);
    expect(container.querySelector('.shelf .row')!.classList.contains('checked')).toBe(false);
  });
});

describe('ListView clearing the basket', () => {
  let restoreAnimations: (() => void) | null = null;

  afterEach(() => {
    restoreAnimations?.();
    restoreAnimations = null;
    vi.useRealTimers();
  });

  it('holds the emptied drawer open long enough for the cascade to play', async () => {
    // Svelte skips a LOCAL transition when the block enclosing it is destroyed,
    // and clearing the basket empties `store.checked` — which destroys exactly
    // the block the rows' staggered farewell lives in. Measured before the fix:
    // clearing nine rows produced zero row animations, and the cascade had never
    // once run. The `clearing` flag keeps that block mounted for the length of
    // the cascade, which is what lets it play at all.
    restoreAnimations = landAnimations();
    vi.useFakeTimers();
    const pile = ['Beer', 'Soda', 'Wine'].map((name) => item({ id: name.toLowerCase(), name }));
    // One row still to get, so emptying the basket does not empty the LIST —
    // the whole shelf-and-drawer block gives way to the empty state then, and
    // there would be no drawer left for `clearing` to be holding open.
    const { store } = movingStore([{ category: 'Drinks', items: [active({ id: 'tea', name: 'Tea' })] }], pile);
    const { container, getByRole } = renderShelf(store);

    await fireEvent.click(getByRole('button', { name: /in the basket/i }));
    await fireEvent.click(getByRole('button', { name: 'Clear' }));
    expect(store.checked).toHaveLength(0);

    // A turn on, with every outro landed: the drawer is still there because
    // `clearing` is holding it open, not because nothing in here ever leaves.
    await settle(0);
    expect(container.querySelector('.checked')).not.toBeNull();

    // And it does not overstay: once the cascade has had its time, it goes.
    await settle(DUR.clear + staggerDelay(pile.length - 1) + 60);
    await settle(DUR.collapse);
    expect(container.querySelector('.checked')).toBeNull();
  });
});

// Every way of deleting one row offers the same undo. The swipe had one from the
// start; the sheet's Remove went straight to the store, so a mistap took the item
// AND retired its shortcut with nothing to press. The shortcut only comes back
// because the undo re-adds inside the server's stash window — so a missing undo
// is not a missing convenience, it is a silently permanent loss of that name's
// history in the tray and in typeahead.
//
// The undo lives in the view's `remove` rather than at the call site, so any
// delete affordance added later inherits it. (ItemRow used to carry a second
// one — an `x`, shown only when no `onOpen` was given, which no call site could
// ever reach because both always pass one. It went with this change.)
describe('ListView single-row delete undo', () => {
  it('offers an undo when the sheet removes the item, restoring name and note', async () => {
    const { store, state } = movingStore([
      { category: 'Drinks', items: [active({ id: 'pk', name: 'Pine Kernels', note: 'the big bag' })] },
    ]);
    const { getByRole } = renderShelf(store);

    await fireEvent.click(getByRole('button', { name: 'Edit Pine Kernels' }));
    await fireEvent.click(getByRole('button', { name: 'Remove' }));
    expect(store.remove).toHaveBeenCalledWith('pk');
    expect(state.groups.flatMap((g) => g.items)).toHaveLength(0);

    // The sheet closes on remove, so the toast has to belong to the list rather
    // than to the sheet that has just gone.
    await fireEvent.click(getByRole('button', { name: 'Undo' }));
    // By name and note, through the normal add path — the same reversal the
    // swipe performs, which is what revives the stashed catalogue row.
    expect(store.add).toHaveBeenCalledWith('Pine Kernels', 'the big bag');
  });

  it('still offers the undo when the store has already dropped the row', async () => {
    // The row went while the sheet was open — a partner deleted it, or an SSE
    // removal raced the tap. An id would have nothing left to look up here, and
    // the delete would go through with no undo: the one silent path the change
    // exists to close. The sheet is holding the row, so it hands that over.
    const { store, state } = movingStore([
      { category: 'Drinks', items: [active({ id: 'pk', name: 'Pine Kernels', note: 'the big bag' })] },
    ]);
    const { getByRole } = renderShelf(store);

    await fireEvent.click(getByRole('button', { name: 'Edit Pine Kernels' }));
    state.groups = [];
    await tick();
    await fireEvent.click(getByRole('button', { name: 'Remove' }));

    expect(store.remove).toHaveBeenCalledWith('pk');
    await fireEvent.click(getByRole('button', { name: 'Undo' }));
    expect(store.add).toHaveBeenCalledWith('Pine Kernels', 'the big bag');
  });
});
