import { render, screen } from '@testing-library/svelte';
import { tick } from 'svelte';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import RecentsGrid from './RecentsGrid.svelte';
import { ApiError } from '../lib/api';
import { mountWithProps } from '../lib/testing/reactiveProps.svelte';
import type { CatalogEntry } from '../lib/types';

/** Every real shortcut pill, clipped or not. The counter is not one of them. */
const PILL = '.pill:not(.more)';
/** The pills a shopper can actually see and press. */
const SHOWN = '.pill:not(.more):not(.clipped)';

function entry(display_name: string): CatalogEntry {
  return {
    name_norm: display_name.toLowerCase(),
    display_name,
    icon: null,
    category: null,
    times_added: 1,
  };
}

/* "Item 0".."Item 23": 72–80px wide under the stand-in layout engine below, so
   three fit on a 300px row and two rows hold six, with 60px still spare at the
   end of row two for the counter. Every expected count is computed from these
   labels by `expectedShown()` rather than written down, so re-wording a fixture
   cannot quietly invalidate the assertions that were read off the old one. */
const many = Array.from({ length: 24 }, (_, i) => entry(`Item ${i}`));

/* The same idea with row two packed tight: eight-character labels are 88px, so
   three of them reach 280px of the 300px row and the counter (48px plus a gap)
   no longer fits beside them. This is the fixture that exercises the counter's
   own room; `many` above deliberately does not. */
const packed = Array.from({ length: 12 }, (_, i) => entry(`Item ${String(i).padStart(3, '0')}`));

/**
 * Retry an assertion while the promise chain behind it drains.
 *
 * Two things `vi.waitFor` cannot do here. It advances timers when fake ones are
 * installed, which would fire the undo window early in every forget test; and
 * the alternative — awaiting a fixed number of microtask turns — pins the SHAPE
 * of the chain rather than its outcome, so adding one `.then` to `commit`
 * breaks tests that have nothing to do with it. This stops the moment the
 * assertion holds, and the ceiling is a backstop rather than a schedule.
 */
async function until(assertion: () => void, rounds = 50): Promise<void> {
  for (let i = 0; ; i += 1) {
    try {
      assertion();
      return;
    } catch (err) {
      if (i >= rounds) throw err;
    }
    await tick();
  }
}

/** Resolves `top()` and lets the component's $effect (and its measurement) settle. */
async function renderTray(entries: CatalogEntry[], activeNames = new Set<string>()) {
  const onPick = vi.fn();
  const onForget = vi.fn().mockResolvedValue(undefined);
  const onError = vi.fn();
  const top = vi.fn(() => Promise.resolve(entries));
  const result = render(RecentsGrid, { top, activeNames, onPick, onForget, onError });
  await until(() => expect(result.container.querySelector(PILL)).toBeTruthy());
  await tick();
  /** Swaps in a new set of already-on-the-list names, as the store does mid-shop. */
  const setActive = (names: Set<string>) =>
    result.rerender({ top, activeNames: names, onPick, onForget, onError });
  /** Tells the tray the catalogue may have moved, as the shell does on an SSE frame. */
  const setRevision = (revision: number) =>
    result.rerender({ top, activeNames, onPick, onForget, onError, revision });
  return { ...result, onPick, onForget, onError, top, setActive, setRevision };
}

describe('RecentsGrid', () => {
  it('names each pill in full, so nothing is guessed from a truncation', async () => {
    // The old fixed-width tiles ellipsised to "Extra-virgin O…", which is the
    // one thing a shortcut must never do: make you open it to know what it is.
    await renderTray([entry('Extra-virgin Olive Oil'), entry('Cottage Cheese')]);
    expect(screen.getByText('Extra-virgin Olive Oil')).toBeTruthy();
    expect(screen.getByText('Cottage Cheese')).toBeTruthy();
  });

  it('adds the item under its display name when a pill is pressed', async () => {
    const { container, onPick } = await renderTray([entry('Sea Salt')]);
    container.querySelector<HTMLButtonElement>(PILL)!.click();
    expect(onPick).toHaveBeenCalledWith('Sea Salt');
  });

  it('hides anything already on the list', async () => {
    const { container } = await renderTray(
      [entry('Pine Kernels'), entry('Red Peppers')],
      new Set(['red peppers']),
    );
    const labels = [...container.querySelectorAll(PILL)].map((p) => p.textContent?.trim());
    expect(labels).toEqual(['Pine Kernels']);
  });

  it('shows every pill where there is no layout to measure', async () => {
    // jsdom does no layout, so every offsetTop reads 0 and the tray cannot tell
    // where row three starts. Failing open (show everything) keeps the tray
    // usable; failing closed would hide the whole catalogue behind a counter.
    const { container } = await renderTray(many);
    expect(container.querySelectorAll(SHOWN).length).toBe(many.length);
    expect(container.querySelector('.more')).toBeNull();
  });

  it('renders nothing at all when every recent is already on the list', async () => {
    // Asserting straight after render would pass even with the filter deleted,
    // because the tray is empty until `top()` resolves. Wait for the fetch to
    // have landed and Svelte to have flushed, so a tray that was ever going to
    // render has had its chance — the same entry DOES render one in the tests
    // above, which is what makes this assertion mean something.
    const top = vi.fn().mockResolvedValue([entry('Sea Salt')]);
    const { container } = render(RecentsGrid, {
      top,
      activeNames: new Set(['sea salt']),
      onPick: vi.fn(),
      onForget: vi.fn(),
      onError: vi.fn(),
    });
    await vi.waitFor(() => expect(top).toHaveBeenCalled());
    await tick();
    expect(container.querySelector('.tray-head')).toBeNull();
  });
});

/*
 * A stand-in flex-wrap engine. jsdom reports every offset as 0, so the two-row
 * cap can only be exercised against a layout we supply ourselves: pills are
 * sized from their label, packed left to right, and wrapped at the tray width.
 * The numbers are arbitrary but fixed, so the expected cap below is arithmetic
 * rather than a guess.
 */
const GAP = 8;
const ROW_HEIGHT = 42;
let trayWidth = 300;

function pillWidth(el: Element): number {
  return 24 + (el.textContent?.trim().length ?? 0) * 8;
}

function place(el: HTMLElement): { left: number; top: number } {
  const tray = el.parentElement;
  if (!tray?.classList.contains('tray')) return { left: 0, top: 0 };
  let x = 0;
  let row = 0;
  for (const child of [...tray.children]) {
    const w = pillWidth(child);
    if (x > 0 && x + w > trayWidth) {
      x = 0;
      row += 1;
    }
    if (child === el) return { left: x, top: row * ROW_HEIGHT };
    x += w + GAP;
  }
  return { left: 0, top: 0 };
}

/** The tray's own box, the way a browser would report it: as many rows as its
 *  UNCLIPPED pills wrap onto. A clipped pill is `display: none` and takes no
 *  space at all, which is exactly why a reading — which unclips every one of
 *  them — changes this number. */
function trayHeight(tray: HTMLElement): number {
  let x = 0;
  let rows = 1;
  for (const child of [...tray.children] as HTMLElement[]) {
    if (child.classList.contains('clipped')) continue;
    const w = pillWidth(child);
    if (x > 0 && x + w > trayWidth) {
      x = 0;
      rows += 1;
    }
    x += w + GAP;
  }
  return rows * ROW_HEIGHT;
}

/** The rows the engine above wraps `labels` into at `width` — the same pack
 *  `place()` walks, said as a list of rows. */
function wrap(labels: string[], width: number): string[][] {
  const rows: string[][] = [[]];
  let x = 0;
  for (const label of labels) {
    const w = 24 + label.length * 8;
    if (x > 0 && x + w > width) {
      rows.push([]);
      x = 0;
    }
    rows[rows.length - 1].push(label);
    x += w + GAP;
  }
  return rows;
}

/** Right edge of the last of `labels` when they are packed from x = 0. */
function rightEdge(labels: string[]): number {
  return labels.reduce((x, l) => x + 24 + l.length * 8 + GAP, 0) - GAP;
}

/**
 * How many pills the tray should end up showing: the ones on the first two
 * wrapped rows, less any that the counter would shove onto row three.
 *
 * Every expected count below is read off this rather than written down, because
 * they are all downstream of one arbitrary number — the `24 + len * 8` pill
 * sizing above. Hard-coded, a one-character change to a fixture label silently
 * invalidated four separate assertions at once.
 */
function expectedShown(labels: string[], width: number): number {
  const rows = wrap(labels, width);
  if (rows.length < 3) return labels.length;
  const onTwoRows = rows[0].length + rows[1].length;
  // The width the counter has mid-reading, when the cap is taken: every pill is
  // unclipped then, so it is labelled with the whole set.
  const counter = 24 + `+${labels.length}`.length * 8;
  let fit = onTwoRows;
  while (fit > 1) {
    const row2 = rows[1].slice(0, Math.max(0, fit - rows[0].length));
    if (rightEdge(row2) + GAP + counter <= width) break;
    fit -= 1;
  }
  return fit;
}

const MANY = many.map((e) => e.display_name);

type ResizeCallback = (entries: ResizeObserverEntry[], observer: ResizeObserver) => void;
let observers: ResizeCallback[] = [];

/** Reports a new tray width to whatever the component is observing. */
function resizeTo(width: number) {
  trayWidth = width;
  for (const cb of observers) {
    cb(
      [{ contentRect: { width } } as ResizeObserverEntry],
      null as unknown as ResizeObserver,
    );
  }
}

const layoutProps = [
  'offsetWidth',
  'offsetLeft',
  'offsetTop',
  'clientWidth',
  'animate',
  'getBoundingClientRect',
] as const;
const original = new Map<string, PropertyDescriptor | undefined>();

/** Swaps in an `element.animate` whose animations land however `land` says. */
function useAnimations(land: (finish: () => void) => void) {
  Object.defineProperty(HTMLElement.prototype, 'animate', {
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
          land(finish);
        },
        oncancel: null,
        currentTime: 0,
        startTime: 0,
        playState: 'finished',
        finished: Promise.resolve(),
      }) as unknown as Animation,
  });
}

describe('RecentsGrid two-row cap', () => {
  beforeEach(() => {
    trayWidth = 300;
    observers = [];
    for (const prop of layoutProps) {
      original.set(prop, Object.getOwnPropertyDescriptor(HTMLElement.prototype, prop));
    }
    Object.defineProperties(HTMLElement.prototype, {
      offsetWidth: {
        configurable: true,
        get(this: HTMLElement) {
          return this.classList.contains('pill') ? pillWidth(this) : trayWidth;
        },
      },
      offsetLeft: {
        configurable: true,
        get(this: HTMLElement) {
          return place(this).left;
        },
      },
      offsetTop: {
        configurable: true,
        get(this: HTMLElement) {
          return place(this).top;
        },
      },
      clientWidth: { configurable: true, get: () => trayWidth },
      getBoundingClientRect: {
        configurable: true,
        writable: true,
        value(this: HTMLElement) {
          const height = this.classList.contains('tray') ? trayHeight(this) : 0;
          return { top: 0, left: 0, right: trayWidth, bottom: height, width: trayWidth, height };
        },
      },
    });
    // The shared setup's `element.animate` stub never calls `onfinish`, so any
    // animation Svelte starts here would sit unfinished forever. Land them on
    // the next turn instead, the way a browser would.
    useAnimations((fn) => setTimeout(fn, 0));
    // The shared setup stubs `element.animate` but not `getAnimations`, which
    // Svelte's `animate:flip` calls when the entry list changes under it.
    if (typeof Element.prototype.getAnimations !== 'function') {
      Object.defineProperty(Element.prototype, 'getAnimations', {
        configurable: true,
        writable: true,
        value: () => [],
      });
    }
    globalThis.ResizeObserver = class {
      constructor(cb: ResizeCallback) {
        observers.push(cb);
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
  });

  afterEach(() => {
    for (const prop of layoutProps) {
      const desc = original.get(prop);
      if (desc) Object.defineProperty(HTMLElement.prototype, prop, desc);
      else delete (HTMLElement.prototype as unknown as Record<string, unknown>)[prop];
    }
  });

  it('shows only the pills that fit on two rows, and counts the rest', async () => {
    const shown = expectedShown(MANY, 300);
    const { container } = await renderTray(many);
    expect(container.querySelectorAll(SHOWN).length).toBe(shown);
    expect(container.querySelector('.more')!.textContent!.trim()).toBe(`+${many.length - shown}`);
  });

  it('keeps room at the end of row two for the counter itself', async () => {
    // The other fixture leaves 60px spare at the end of row two, so it never
    // exercises the shove: with the counter-room loop deleted outright it
    // reported the same six pills. These labels fill row two to 280 of the 300px
    // tray, and the counter needs 48 plus a gap — so the pill that would sit
    // beside it joins the count instead.
    const labels = packed.map((e) => e.display_name);
    const onTwoRows = wrap(labels, 300).slice(0, 2).flat().length;
    const shown = expectedShown(labels, 300);
    // The fixture really is tight: without the shove it would show one more.
    expect(shown).toBe(onTwoRows - 1);

    const { container } = await renderTray(packed);
    expect(container.querySelectorAll(SHOWN).length).toBe(shown);
    expect(container.querySelector('.more')!.textContent!.trim()).toBe(`+${packed.length - shown}`);
  });

  it('keeps the counter out of the shortcut vocabulary and says what it does', async () => {
    // "+18" alone tells a screen-reader user nothing about what pressing it does,
    // and read as a shortcut it sounds like an item called "plus eighteen".
    await renderTray(many);
    const hidden = many.length - expectedShown(MANY, 300);
    const more = screen.getByRole('button', { name: `Show ${hidden} more` });
    expect(more.getAttribute('aria-expanded')).toBe('false');
  });

  it('expands to the whole set when the counter is pressed, and back again', async () => {
    const { container } = await renderTray(many);
    const more = container.querySelector<HTMLButtonElement>('.more')!;
    more.click();
    await tick();
    expect(container.querySelectorAll(SHOWN).length).toBe(many.length);
    // The way back is the control you just pressed, in the place you left it.
    const less = screen.getByRole('button', { name: /fewer/i });
    expect(less.getAttribute('aria-expanded')).toBe('true');
    less.click();
    await tick();
    await tick();
    expect(container.querySelectorAll(SHOWN).length).toBe(expectedShown(MANY, 300));
  });

  it('marks the pills past row two rather than dropping them from the tray', async () => {
    // They stay in the DOM because a reading needs them there: the tray can
    // only find where row three starts while it is holding every pill. The
    // marker class is what hides them, with `display: none` — no space, no
    // focus, no announcement. (Component styles aren't injected under vitest,
    // so the class is as far as this can assert; the rule itself lives in the
    // stylesheet.)
    const { container } = await renderTray(many);
    expect(container.querySelectorAll('.pill.clipped').length).toBe(
      many.length - expectedShown(MANY, 300),
    );
    expect(container.querySelectorAll(PILL).length).toBe(many.length);
  });

  it('re-measures when the viewport narrows', async () => {
    const { container } = await renderTray(many);
    expect(container.querySelectorAll(SHOWN).length).toBe(expectedShown(MANY, 300));
    // A narrower tray fits fewer pills per row, so two rows hold fewer of them.
    const narrowed = expectedShown(MANY, 150);
    expect(narrowed).toBeLessThan(expectedShown(MANY, 300));
    resizeTo(150);
    await vi.waitFor(() => expect(container.querySelectorAll(SHOWN).length).toBe(narrowed));
    expect(container.querySelector('.more')!.textContent!.trim()).toBe(
      `+${many.length - narrowed}`,
    );
  });

  it('measures when the entries land, not only when the tray mounts', async () => {
    // The catalogue arrives a fetch after mount, so the first tray the effect
    // ever sees is an empty one. A cap taken only at mount would be Infinity
    // for the whole session.
    let deliver: (entries: CatalogEntry[]) => void = () => {};
    const { container } = render(RecentsGrid, {
      top: () => new Promise<CatalogEntry[]>((resolve) => (deliver = resolve)),
      activeNames: new Set<string>(),
      onPick: vi.fn(),
      onForget: vi.fn(),
      onError: vi.fn(),
    });
    await tick();
    expect(container.querySelector(PILL)).toBeNull();
    deliver(many);
    const shown = expectedShown(MANY, 300);
    await vi.waitFor(() => expect(container.querySelectorAll(SHOWN).length).toBe(shown));
    expect(container.querySelector('.more')!.textContent!.trim()).toBe(`+${many.length - shown}`);
  });

  it('holds its own height while it takes a reading', async () => {
    // A reading renders every pill, which is three or four rows rather than two,
    // and everything below the tray in <main> sits ~170px lower for that flush.
    // Nothing paints it — but the list below MEASURES itself in the same flush:
    // Svelte's `animate:flip` on the aisle rows takes its "from" rects while the
    // tray is unclipped and its "to" rects after it has collapsed, decides every
    // row in the list moved, and eases the whole shelf back from a displacement
    // that never happened. Checking an item off drops its name out of
    // `activeNames`, which is what invalidates the reading — so every check-off
    // set the entire list spinning. Pinning the height makes a reading cost the
    // document nothing.
    //
    // Only the pin is assertable here: jsdom does no layout, so the FLIP it
    // corrupts (and the spin itself) can only be seen in a real browser.
    const { container, setActive } = await renderTray(many);
    const tray = container.querySelector<HTMLElement>('.tray')!;
    const settled = tray.getBoundingClientRect().height;

    // What the tray looked like each time `twoRowCap` reached for a pill's
    // position — the moment the rest of the page is being measured around it.
    const readings: { pinned: string; shown: number }[] = [];
    const offsetTop = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetTop')!;
    Object.defineProperty(HTMLElement.prototype, 'offsetTop', {
      configurable: true,
      get(this: HTMLElement) {
        if (this.classList.contains('pill')) {
          readings.push({ pinned: tray.style.height, shown: tray.querySelectorAll(SHOWN).length });
        }
        return offsetTop.get!.call(this);
      },
    });
    await setActive(new Set(['item 0']));
    Object.defineProperty(HTMLElement.prototype, 'offsetTop', offsetTop);

    // The reading really is taken on an unclipped tray — more pills than the two
    // rows hold, which is the displacement the pin exists to absorb.
    expect(Math.max(...readings.map((r) => r.shown))).toBeGreaterThan(expectedShown(MANY, 300));
    // And every one of those measurements happened at the settled height.
    expect([...new Set(readings.map((r) => r.pinned))]).toEqual([`${settled}px`]);
  });

  it('re-measures when the list claims most of the tray', async () => {
    // Mid-shop the list eats the tray from underneath: `activeNames` filters
    // entries out and the survivors re-wrap onto one row, so a cap measured
    // once goes stale and would keep counting pills that are no longer there.
    const { container, setActive } = await renderTray(many);
    expect(container.querySelector('.more')).toBeTruthy();
    await setActive(new Set(many.slice(0, 21).map((e) => e.name_norm)));
    await vi.waitFor(() => expect(container.querySelector('.more')).toBeNull());
    for (const name of ['Item 21', 'Item 22', 'Item 23']) {
      expect(screen.getByText(name).classList.contains('clipped')).toBe(false);
    }
  });
});

/*
 * Forgetting a shortcut.
 *
 * The tray is fed by the catalogue, which learns from every add — including the
 * ones the ring mis-heard ("Marty Rice" for basmati rice). Those have no item to
 * delete, so until the gesture below existed they were offered forever.
 */

/** A pointer event as a touchscreen sends it. jsdom has no PointerEvent, and a
 *  MouseEvent carries the coordinates already; the two pointer fields the
 *  handlers read are pinned on afterwards. */
function touch(type: string, opts: { x?: number; y?: number; id?: number } = {}): MouseEvent {
  const ev = new MouseEvent(type, { bubbles: true, clientX: opts.x ?? 0, clientY: opts.y ?? 0 });
  Object.defineProperty(ev, 'pointerType', { value: 'touch' });
  Object.defineProperty(ev, 'pointerId', { value: opts.id ?? 1 });
  return ev;
}

/** A pointer event as a mouse sends it — the desktop half of the gesture pair. */
function mouse(type: string, button = 0): MouseEvent {
  const ev = new MouseEvent(type, { bubbles: true, cancelable: true, button });
  Object.defineProperty(ev, 'pointerType', { value: 'mouse' });
  Object.defineProperty(ev, 'pointerId', { value: 1 });
  return ev;
}

/** Right-click something the way a browser does: the pointerdown that precedes
 *  the menu, then the menu event itself — which is returned so a test can ask
 *  whether the browser's own menu was suppressed. */
function rightClick(el: HTMLElement): MouseEvent {
  el.dispatchEvent(mouse('pointerdown', 2));
  const menu = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, button: 2 });
  el.dispatchEvent(menu);
  return menu;
}

/* The component's own numbers, restated here only so the tests that are not
   ABOUT the numbers can say "past the threshold" / "past the window" in one
   place. Both boundaries are pinned with literals instead — see "fires the hold
   at 300ms and not a millisecond sooner" and "waits the full five seconds
   before it tells the server" — because a copy of a constant agrees with
   whatever the component says, including a regression. */
const HOLD_MS = 300;
const UNDO_MS = 5000;

function pillNamed(container: Element, label: string): HTMLButtonElement {
  const pill = [...container.querySelectorAll<HTMLButtonElement>(PILL)].find(
    (p) => p.textContent?.trim() === label,
  );
  if (!pill) throw new Error(`no pill labelled "${label}"`);
  return pill;
}

function labels(container: Element): string[] {
  return [...container.querySelectorAll(PILL)].map((p) => p.textContent!.trim());
}

/** Give every pending continuation its chance to run, for the assertions that
 *  are NEGATIVE — nothing to wait for, so `until` would return on the first
 *  turn. The count is a generous ceiling on the depth of the promise chain a
 *  commit settles through, not a description of it. */
async function quiesce(turns = 20) {
  for (let i = 0; i < turns; i += 1) await tick();
}

/** Every live undo toast, by the name it announces — never by DOM order. The
 *  shell can be showing one of its own, and two forgets in a row put a second
 *  one up while the first is still settling. */
function toastNaming(container: Element, label: string): Element | null {
  return (
    [...container.ownerDocument.querySelectorAll('[role="status"]')].find((t) =>
      t.textContent?.includes(label),
    ) ?? null
  );
}

/** Press and hold a pill past the fire threshold, as a thumb does. */
async function hold(pill: HTMLElement, ms = HOLD_MS) {
  pill.dispatchEvent(touch('pointerdown'));
  vi.advanceTimersByTime(ms);
  await tick();
}

const junk = [entry('Marty Rice'), entry('Papa Dums'), entry('Coffee')];

describe('RecentsGrid — forgetting a shortcut', () => {
  beforeEach(() => {
    localStorage.clear();
    // Installed before `render`, not after it: a clock swapped mid-life leaves
    // whatever the component scheduled during mount on the real one, so a test
    // ends up advancing a clock nothing is listening to.
    vi.useFakeTimers();
  });
  afterEach(() => vi.useRealTimers());

  it('takes the pill off the tray on a long press, and only then asks the server', async () => {
    const { container, onForget } = await renderTray(junk);

    await hold(pillNamed(container, 'Marty Rice'));
    expect(labels(container)).toEqual(['Papa Dums', 'Coffee']);
    // Nothing has gone to the wire yet: the undo window is the commit window,
    // so an undo has nothing to reverse and cannot itself fail.
    expect(onForget).not.toHaveBeenCalled();

    vi.advanceTimersByTime(UNDO_MS);
    await tick();
    expect(onForget).toHaveBeenCalledWith('marty rice');
  });

  it('waits the full five seconds before it tells the server, to the millisecond', async () => {
    // The literals are the point. Every other test here says `UNDO_MS`, which
    // is this file's own copy of the number and therefore agrees with the
    // component whatever the component says — a window cut to half a second
    // (long enough to read the toast, nowhere near long enough to reach for the
    // Undo button) would sail through all of them.
    const { container, onForget } = await renderTray(junk);
    await hold(pillNamed(container, 'Marty Rice'));

    vi.advanceTimersByTime(4999);
    await quiesce();
    expect(onForget).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    await until(() => expect(onForget).toHaveBeenCalledWith('marty rice'));
  });

  it('fires the hold at 300ms and not a millisecond sooner', async () => {
    // Same reasoning as the undo window: `HOLD_MS` here is a copy. A threshold
    // that crept down to 250ms would start taking shortcuts away from ordinary
    // slow taps, and every test that reaches for the constant would agree that
    // it was fine.
    const { container } = await renderTray(junk);
    const pill = pillNamed(container, 'Marty Rice');
    pill.dispatchEvent(touch('pointerdown'));

    vi.advanceTimersByTime(299);
    await quiesce();
    expect(labels(container)).toContain('Marty Rice');

    vi.advanceTimersByTime(1);
    await until(() => expect(labels(container)).toEqual(['Papa Dums', 'Coffee']));
  });

  it('leaves the pill gone once the forget has been accepted', async () => {
    // The tray's own copy of the catalogue has to lose the row too. Hiding it
    // in `forgetting` alone puts the pill back the moment that set is emptied
    // — five seconds after you forgot it, right where you are still looking.
    const { container, onForget } = await renderTray(junk);
    await hold(pillNamed(container, 'Marty Rice'));

    vi.advanceTimersByTime(UNDO_MS);
    await until(() => expect(onForget).toHaveBeenCalledWith('marty rice'));
    await quiesce();
    expect(labels(container)).toEqual(['Papa Dums', 'Coffee']);
  });

  it('treats a shortcut that has already gone as forgotten, not as a failure', async () => {
    // A repeat forget is routine: the unload flush sends one and the five-second
    // timer fires again behind it, or the other phone on the counter forgot it
    // first. A
    // 404 is the outcome we wanted, so the pill must stay gone and the shell
    // must stay quiet — "Couldn't forget" with the pill back is the one thing
    // that would make the shopper do it all again.
    const { container, onForget, onError } = await renderTray(junk);
    onForget.mockRejectedValue(new ApiError(404, 'Shortcut not found'));
    await hold(pillNamed(container, 'Marty Rice'));

    vi.advanceTimersByTime(UNDO_MS);
    await until(() => expect(onForget).toHaveBeenCalledWith('marty rice'));
    await quiesce();
    expect(labels(container)).toEqual(['Papa Dums', 'Coffee']);
    expect(onError).not.toHaveBeenCalled();
  });

  it('lands the first forget when a second one follows straight after', async () => {
    // Tidying two bad transcriptions in a row: one pending forget at a time, so
    // the second must COMMIT the first rather than replace it. Replacing it
    // loses the first outright — hidden locally, never sent, and back at the
    // next refetch.
    const { container, onForget } = await renderTray(junk);
    await hold(pillNamed(container, 'Marty Rice'));
    await hold(pillNamed(container, 'Papa Dums'));

    // Sent the moment the second forget started, with no clock advanced at all.
    await until(() => expect(onForget).toHaveBeenCalledWith('marty rice'));
    expect(onForget).toHaveBeenCalledTimes(1);
    // …and the second is still in its own undo window, with its own way back.
    expect(toastNaming(container, 'Papa Dums')).toBeTruthy();
    vi.advanceTimersByTime(UNDO_MS);
    await until(() => expect(onForget).toHaveBeenCalledWith('papa dums'));
    await quiesce();
    expect(labels(container)).toEqual(['Coffee']);
  });

  it('says what it forgot, out loud, with a way back', async () => {
    const { container } = await renderTray(junk);
    await hold(pillNamed(container, 'Marty Rice'));

    // role="status" (Toast) is what carries this to a screen reader — the pill
    // simply vanishing announces nothing at all. Found by the name it says, so
    // a second toast on the page cannot answer for it.
    const toast = toastNaming(container, 'Marty Rice')!;
    expect(toast).toBeTruthy();
    expect(toast.querySelector('button')!.textContent).toMatch(/undo/i);
  });

  it('puts the pill back on undo, and never tells the server', async () => {
    const { container, onForget } = await renderTray(junk);
    await hold(pillNamed(container, 'Marty Rice'));

    const undo = toastNaming(container, 'Marty Rice')!.querySelector('button')!;
    undo.click();
    await tick();
    expect(labels(container)).toEqual(['Marty Rice', 'Papa Dums', 'Coffee']);

    vi.advanceTimersByTime(UNDO_MS * 2);
    await tick();
    expect(onForget).not.toHaveBeenCalled();
  });

  it('does not also add the item when the hold has fired', async () => {
    // The pill is tap-to-add, and a hold ends in the same pointerup a tap does.
    // Without the guard, forgetting a shortcut would add something you never
    // asked for — and re-create its catalogue row on the way.
    //
    // The click is driven on the NEIGHBOUR, re-queried after the hold, because
    // that is where the browser actually delivers it: the forgotten pill leaves
    // the DOM the instant it is hidden, the tray re-wraps, and whichever pill
    // slid into the gap is what is under the finger at pointerup. (It is also
    // the only version of this test that can fail: clicking the captured — and
    // by now detached — node reaches no Svelte handler at all, so it passed
    // just as happily with the guard deleted outright.)
    const { container, onPick } = await renderTray(junk);
    await hold(pillNamed(container, 'Marty Rice'));

    // Only the click is dispatched, not the pointerup that carries it: the
    // press spring's settle promise never resolves without a rAF loop, so a
    // pointerup here leaves it pending and the next pointerdown aborts it into
    // an unhandled rejection that has nothing to do with this test. The click
    // is the event the guard is about either way.
    const neighbour = pillNamed(container, 'Papa Dums');
    neighbour.click();
    await quiesce();
    expect(onPick).not.toHaveBeenCalled();

    // And the swallow is spent on that one click: the next deliberate tap on
    // the same pill adds it, or the gesture would have cost the shopper a
    // shortcut they never touched.
    neighbour.dispatchEvent(touch('pointerdown'));
    neighbour.click();
    await tick();
    expect(onPick).toHaveBeenCalledWith('Papa Dums');
  });

  it('still adds on a short press', async () => {
    const { container, onPick } = await renderTray(junk);
    const pill = pillNamed(container, 'Marty Rice');
    pill.dispatchEvent(touch('pointerdown'));
    vi.advanceTimersByTime(HOLD_MS - 100);
    pill.dispatchEvent(touch('pointerup'));
    pill.click();
    await tick();
    expect(onPick).toHaveBeenCalledWith('Marty Rice');
    expect(labels(container)).toContain('Marty Rice');
  });

  it('reads a moving finger as a scroll, not a hold', async () => {
    // The tray sits at the top of a scrolling list. A thumb that lands on a pill
    // and drags the page must not lose the shortcut it happened to touch.
    const { container, onForget } = await renderTray(junk);
    const pill = pillNamed(container, 'Marty Rice');
    pill.dispatchEvent(touch('pointerdown', { x: 0, y: 0 }));
    pill.dispatchEvent(touch('pointermove', { x: 0, y: 40 }));
    vi.advanceTimersByTime(HOLD_MS * 3);
    await tick();
    expect(labels(container)).toContain('Marty Rice');
    expect(onForget).not.toHaveBeenCalled();
  });

  it('ignores a held mouse button', async () => {
    // 300ms is an ordinary slow click with a mouse, and losing a shortcut to one
    // would be a trap. Pointer devices forget with the Delete key instead —
    // lib/drag.svelte.ts splits touch from mouse the same way.
    const { container } = await renderTray(junk);
    const pill = pillNamed(container, 'Marty Rice');
    const down = new MouseEvent('pointerdown', { bubbles: true });
    Object.defineProperty(down, 'pointerType', { value: 'mouse' });
    Object.defineProperty(down, 'pointerId', { value: 1 });
    pill.dispatchEvent(down);
    vi.advanceTimersByTime(HOLD_MS * 3);
    await tick();
    expect(labels(container)).toContain('Marty Rice');
  });

  it('forgets on a right-click, through the same undo window as the hold', async () => {
    // The desktop gap the hold leaves: with a mouse there was no pointer gesture
    // at all, and clicking a pill to focus it for the Delete key ADDS the item —
    // so the keyboard path was only reachable by tabbing past every other pill.
    // Right-click is the conventional "do anything but activate this", and it
    // routes into exactly the same optimistic hide and deferred commit.
    const { container, onForget } = await renderTray(junk);

    rightClick(pillNamed(container, 'Marty Rice'));
    await tick();
    expect(labels(container)).toEqual(['Papa Dums', 'Coffee']);
    const toast = toastNaming(container, 'Marty Rice')!;
    expect(toast).toBeTruthy();
    expect(toast.querySelector('button')!.textContent).toMatch(/undo/i);
    expect(onForget).not.toHaveBeenCalled();

    vi.advanceTimersByTime(UNDO_MS);
    await tick();
    expect(onForget).toHaveBeenCalledWith('marty rice');
  });

  it('suppresses the browser menu on a pill and nowhere else', async () => {
    // The OS callout would land on top of the undo toast at the moment the
    // forget fires. Everywhere else in the tray the menu is the browser's own
    // business — copying a heading, inspecting the page.
    const { container } = await renderTray(junk);

    expect(rightClick(pillNamed(container, 'Marty Rice')).defaultPrevented).toBe(true);
    for (const selector of ['.tray', '.tray-head']) {
      const el = container.querySelector<HTMLElement>(selector)!;
      expect(rightClick(el).defaultPrevented).toBe(false);
    }
  });

  it('does not forget twice when a touch long press raises its own menu', async () => {
    // Some browsers fire a synthetic contextmenu off a touch long press — the
    // very press that has just forgotten the pill. Acting on it would forget a
    // second shortcut: the tray re-wraps the instant a pill leaves, so the
    // second forget lands on whichever pill slid into the empty slot.
    const { container, onForget } = await renderTray(junk);
    await hold(pillNamed(container, 'Marty Rice'));
    // The menu arrives after the pill it was raised on has already gone, so the
    // browser dispatches it at whatever is under the finger now — which is the
    // pill that closed up into the gap.
    pillNamed(container, 'Papa Dums').dispatchEvent(
      new MouseEvent('contextmenu', { bubbles: true, cancelable: true }),
    );
    await tick();

    expect(labels(container)).toEqual(['Papa Dums', 'Coffee']);
    vi.advanceTimersByTime(UNDO_MS);
    await until(() => expect(onForget).toHaveBeenCalledWith('marty rice'));
    await quiesce();
    expect(onForget).toHaveBeenCalledTimes(1);
  });

  it('lets the hold finish when the synthetic menu arrives before it fires', async () => {
    // The other ordering: a browser whose long-press menu comes up first. The
    // hold owns the gesture either way, so this must forget once, not twice.
    const { container, onForget } = await renderTray(junk);
    const pill = pillNamed(container, 'Marty Rice');
    pill.dispatchEvent(touch('pointerdown'));
    vi.advanceTimersByTime(HOLD_MS - 100);
    pill.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
    await tick();
    expect(labels(container)).toEqual(['Marty Rice', 'Papa Dums', 'Coffee']);

    vi.advanceTimersByTime(HOLD_MS);
    await tick();
    expect(labels(container)).toEqual(['Papa Dums', 'Coffee']);
    vi.advanceTimersByTime(UNDO_MS);
    await until(() => expect(onForget).toHaveBeenCalledWith('marty rice'));
    await quiesce();
    expect(onForget).toHaveBeenCalledTimes(1);
  });

  it('still adds on the next left click after a right-click forget', async () => {
    // Documentation of intent, not a guard — say so plainly, because the
    // comment here used to claim one. A right-click raises no click event, so
    // `contextMenu` has nothing to swallow and deliberately leaves the flag
    // alone. But the pointerdown that PRECEDES any contextmenu clears the flag
    // anyway (and this test dispatches one, as a browser does), so the hazard
    // it describes cannot occur in a browser and cannot be reproduced here:
    // setting `fired` in `contextMenu` leaves this green. The guard against
    // eating a click lives in the hold test above; what this pins is that the
    // desktop forget and the desktop add stay independent gestures.
    const { container, onPick } = await renderTray(junk);
    rightClick(pillNamed(container, 'Marty Rice'));
    await tick();

    const pill = pillNamed(container, 'Coffee');
    pill.dispatchEvent(mouse('pointerdown'));
    pill.dispatchEvent(mouse('pointerup'));
    pill.click();
    await tick();
    expect(onPick).toHaveBeenCalledWith('Coffee');
  });

  it('forgets from the keyboard, and moves focus on', async () => {
    const { container } = await renderTray(junk);
    const pill = pillNamed(container, 'Papa Dums');
    pill.focus();
    pill.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true }));
    await until(() => expect(labels(container)).toEqual(['Marty Rice', 'Coffee']));
    // Focus must land somewhere real: the button that took the forgotten one's
    // place, not <body>, or the tab position is lost mid-tidy.
    await until(() => expect(document.activeElement).toBe(pillNamed(container, 'Coffee')));
  });

  it('accepts Backspace as well, since half the world reaches for it', async () => {
    const { container } = await renderTray(junk);
    const pill = pillNamed(container, 'Coffee');
    pill.focus();
    pill.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace', bubbles: true }));
    await tick();
    expect(labels(container)).toEqual(['Marty Rice', 'Papa Dums']);
  });

  it('brings the pill back and says so when the server refuses', async () => {
    const { container, onForget, onError } = await renderTray(junk);
    onForget.mockRejectedValue(new Error('offline'));
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});

    await hold(pillNamed(container, 'Marty Rice'));
    vi.advanceTimersByTime(UNDO_MS);
    await until(() =>
      expect(onError).toHaveBeenCalledWith(expect.stringContaining('Marty Rice')),
    );

    expect(labels(container)).toContain('Marty Rice');
    // The notice goes to the shell's error channel, not to a toast of the
    // tray's own — see the two tests below for the two ways the tray's slot
    // loses the message.
    expect(onError).toHaveBeenCalledWith(expect.stringMatching(/couldn't forget/i));
    expect(onError).toHaveBeenCalledWith(expect.stringContaining('Marty Rice'));
    // A shortcut that is still being offered after you told it to go is exactly
    // the failure this feature exists to fix; it never passes silently.
    expect(err).toHaveBeenCalled();
    err.mockRestore();
  });

  it('still says so when the failure lands after the tray has gone', async () => {
    // The flush-on-unmount path is the one the deferred commit exists to
    // protect, and it was the only path whose failure said nothing at all: the
    // tray owns the toast, and the tray is what has just been unmounted.
    // Typing one character in the add bar is enough to get here.
    const { container, onForget, onError, unmount } = await renderTray(junk);
    onForget.mockRejectedValue(new Error('offline'));
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});

    await hold(pillNamed(container, 'Marty Rice'));
    unmount();
    await until(() =>
      expect(onError).toHaveBeenCalledWith(expect.stringContaining('Marty Rice')),
    );

    expect(err).toHaveBeenCalled();
    err.mockRestore();
  });

  it('does not let a second forget swallow the first one’s failure', async () => {
    // Tidying two bad transcriptions in a row is the likeliest way to use this
    // at all. The second forget lands the first, and while the second's undo
    // toast holds the slot for its whole five seconds the first's failure
    // notice has nowhere to go.
    const { container, onForget, onError } = await renderTray(junk);
    onForget.mockRejectedValue(new Error('offline'));
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});

    await hold(pillNamed(container, 'Marty Rice'));
    await hold(pillNamed(container, 'Papa Dums'));
    await until(() =>
      expect(onError).toHaveBeenCalledWith(expect.stringContaining('Marty Rice')),
    );

    // Marty Rice's commit has been fired and refused…
    expect(onForget).toHaveBeenCalledWith('marty rice');
    // …and Papa Dums still owns the tray's own undo slot, undisturbed. Found by
    // the name it announces, not by being the first [role="status"] in the
    // document — which is exactly the confusion this test is about.
    expect(toastNaming(container, 'Papa Dums')).toBeTruthy();
    err.mockRestore();
  });

  it('commits a forget that is still pending when the tray goes away', async () => {
    // Typing in the add bar unmounts the tray. The undo window dying with it
    // would quietly drop the forget, and the junk would be back next time.
    const { container, onForget, unmount } = await renderTray(junk);
    await hold(pillNamed(container, 'Marty Rice'));
    unmount();
    expect(onForget).toHaveBeenCalledWith('marty rice');
  });

  it('flushes a pending forget when the page is going away', async () => {
    // onDestroy does not fire for a closing tab, a killed PWA, or iOS freezing
    // a backgrounded page — and a frozen page does not run the five-second
    // timer either. Pocket the phone inside the undo window and the request was
    // never made at all: no error, no retry, no record.
    const { container, onForget } = await renderTray(junk);
    await hold(pillNamed(container, 'Marty Rice'));
    expect(onForget).not.toHaveBeenCalled();

    window.dispatchEvent(new Event('pagehide'));
    await quiesce();
    // keepalive, so the request survives the navigation that is already under
    // way rather than being cancelled with the document.
    expect(onForget).toHaveBeenCalledWith('marty rice', { keepalive: true });
  });

  it('flushes a pending forget when the tab is hidden', async () => {
    // The pocketed-phone case proper: the page is not unloading, it is being
    // frozen, and pagehide is not guaranteed for it.
    const { container, onForget } = await renderTray(junk);
    await hold(pillNamed(container, 'Marty Rice'));

    const state = Object.getOwnPropertyDescriptor(Document.prototype, 'visibilityState');
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    try {
      document.dispatchEvent(new Event('visibilitychange'));
      await quiesce();
      expect(onForget).toHaveBeenCalledWith('marty rice', { keepalive: true });
    } finally {
      if (state) Object.defineProperty(Document.prototype, 'visibilityState', state);
      delete (document as unknown as Record<string, unknown>).visibilityState;
    }
  });

  it('does not flush on a page that is merely coming back into view', async () => {
    const { container, onForget } = await renderTray(junk);
    await hold(pillNamed(container, 'Marty Rice'));
    document.dispatchEvent(new Event('visibilitychange')); // still 'visible'
    await quiesce();
    expect(onForget).not.toHaveBeenCalled();
  });

  it('moves focus to the way back when the last shortcut goes', async () => {
    // Forgetting the only candidate unmounts the whole tray block, so the next
    // pill, the previous pill and the counter are all gone at once and focus
    // fell onto <body>. The undo button outlives the tray and is the one
    // control still to do with what just happened.
    const { container } = await renderTray([entry('Marty Rice')]);
    const pill = pillNamed(container, 'Marty Rice');
    pill.focus();
    pill.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true }));
    await until(() => expect(toastNaming(container, 'Marty Rice')).toBeTruthy());

    const undo = toastNaming(container, 'Marty Rice')!.querySelector('button')!;
    expect(undo.textContent).toMatch(/undo/i);
    await until(() => expect(document.activeElement).toBe(undo));
  });

  it('names the gesture this device actually has', async () => {
    // One line, and only the gesture that is real here: a mouse cannot hold
    // (300ms is an ordinary slow click) and a touchscreen has no right button.
    // The contract is which gesture is named, not the wording — an exact string
    // here failed on a comma and passed on a hint that taught the wrong device.
    const fine = await renderTray(junk);
    const desktopHint = fine.container.querySelector('.hint')!.textContent!;
    expect(desktopHint).toMatch(/right.?click/i);
    expect(desktopHint).not.toMatch(/\bhold\b/i);
    fine.unmount();

    const media = window.matchMedia;
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: (query: string) =>
        ({ ...media(query), matches: query.includes('coarse') }) as MediaQueryList,
    });
    try {
      const touchy = await renderTray(junk);
      const touchHint = touchy.container.querySelector('.hint')!.textContent!;
      expect(touchHint).toMatch(/\bhold\b/i);
      expect(touchHint).not.toMatch(/right.?click/i);
    } finally {
      Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: media });
    }
  });

  it('offers the gesture once and then stops mentioning it', async () => {
    // The tray's whole job is being quick to scan, so the hint lives on the
    // heading (chrome you already skip) rather than on the pills, and retires
    // itself the moment it has been acted on.
    const { container, unmount } = await renderTray(junk);
    expect(container.querySelector('.tray-head')!.textContent).toMatch(/forget/i);

    await hold(pillNamed(container, 'Marty Rice'));
    expect(container.querySelector('.tray-head')!.textContent).not.toMatch(/forget/i);

    unmount();
    const again = await renderTray(junk);
    expect(again.container.querySelector('.tray-head')!.textContent).not.toMatch(/forget/i);
  });
});

describe('RecentsGrid — keeping up with the catalogue', () => {
  afterEach(() => vi.useRealTimers());

  it('refetches when the shell says the catalogue may have moved', async () => {
    // Deleting an item removes its catalogue row server-side, but the tray is a
    // fetch and not a subscription: without this the pill stayed on screen until
    // the component remounted, so the owner deletes the junk, watches the tray,
    // and concludes nothing happened.
    const onPick = vi.fn();
    const onForget = vi.fn();
    let offered = [entry('Marty Rice'), entry('Coffee')];
    const top = vi.fn(() => Promise.resolve(offered));
    const props = {
      top,
      activeNames: new Set<string>(),
      onPick,
      onForget,
      onError: vi.fn(),
      revision: 0,
    };
    const { container, rerender } = render(RecentsGrid, props);
    await vi.waitFor(() => expect(labels(container)).toEqual(['Marty Rice', 'Coffee']));

    offered = [entry('Coffee')];
    await rerender({ ...props, revision: 1 });
    await vi.waitFor(() => expect(labels(container)).toEqual(['Coffee']));
    expect(top).toHaveBeenCalledTimes(2);
  });

  it('refetches for the revision and for nothing else', async () => {
    // The test above cannot tell the two apart: `rerender` re-seats the whole
    // props record, so the fetch effect re-runs on ANY rerender and the line
    // that reads `revision` can be deleted with it still green. Mounted with
    // one signal per prop — the way a parent feeds it — the difference is
    // visible, and it is the difference between "a check-off costs no request"
    // and one GET per tap on a phone with two bars in a carpark.
    const top = vi.fn(() => Promise.resolve(junk));
    const tray = mountWithProps(RecentsGrid, {
      top,
      activeNames: new Set<string>(),
      onPick: vi.fn(),
      onForget: vi.fn(),
      onError: vi.fn(),
      revision: 0,
    });
    try {
      await until(() => expect(top).toHaveBeenCalledTimes(1));

      // Checking something off changes the names on the list, and nothing else.
      tray.props.activeNames = new Set(['coffee']);
      await quiesce();
      expect(top).toHaveBeenCalledTimes(1);

      // The shell says the catalogue may have moved.
      tray.props.revision = 1;
      await until(() => expect(top).toHaveBeenCalledTimes(2));
    } finally {
      tray.unmount();
    }
  });

  it('keeps the pills it has when a refetch cannot reach the server', async () => {
    // Mid-shop in a carpark, a failed refetch used to empty `entries` and take
    // the whole tray with it — the shortcuts are still perfectly good, they are
    // just as old as the last successful fetch.
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    const onPick = vi.fn();
    const onForget = vi.fn();
    let fail = false;
    const top = vi.fn(() => (fail ? Promise.reject(new Error('offline')) : Promise.resolve(junk)));
    const props = {
      top,
      activeNames: new Set<string>(),
      onPick,
      onForget,
      onError: vi.fn(),
      revision: 0,
    };
    const { container, rerender } = render(RecentsGrid, props);
    await vi.waitFor(() => expect(labels(container).length).toBe(3));

    fail = true;
    await rerender({ ...props, revision: 1 });
    await vi.waitFor(() => expect(top).toHaveBeenCalledTimes(2));
    await tick();
    expect(labels(container).length).toBe(3);
    expect(err).toHaveBeenCalled();
    err.mockRestore();
  });

  it('keeps a pending forget hidden across a refetch that still offers it', async () => {
    // The refetch is triggered by SSE frames the shopper's own adds produce, so
    // one can easily land inside the five-second undo window — where the server
    // has not been told yet and will happily offer the pill straight back.
    const { container, setRevision } = await renderTray(junk);
    vi.useFakeTimers();
    await hold(pillNamed(container, 'Marty Rice'));
    vi.useRealTimers();

    await setRevision(1);
    await vi.waitFor(() => expect(labels(container)).toEqual(['Papa Dums', 'Coffee']));
  });

  it('drops a refetch that was already in flight when the forget committed', async () => {
    // A refetch issued at t≈4.9s asked a server that still had the row; the
    // DELETE lands at t=5.0s and the answer arrives after it, reassigning
    // `entries` wholesale and re-offering a shortcut the server has already
    // deleted. Tapping it re-adds the item AND re-creates the catalogue row,
    // undoing the forget for good.
    const onPick = vi.fn();
    const onError = vi.fn();
    const onForget = vi.fn().mockResolvedValue(undefined);
    let answerLate: (entries: CatalogEntry[]) => void = () => {};
    let calls = 0;
    const top = vi.fn(() => {
      calls += 1;
      if (calls === 1) return Promise.resolve(junk);
      return new Promise<CatalogEntry[]>((resolve) => (answerLate = resolve));
    });
    const props = { top, activeNames: new Set<string>(), onPick, onForget, onError, revision: 0 };
    const { container, rerender } = render(RecentsGrid, props);
    await vi.waitFor(() => expect(labels(container).length).toBe(3));

    vi.useFakeTimers();
    await hold(pillNamed(container, 'Marty Rice'));

    // Inside the undo window: the refetch goes out while the server still has
    // the row, so its answer will contain it.
    await rerender({ ...props, revision: 1 });
    await until(() => expect(top).toHaveBeenCalledTimes(2));

    // The window closes and the DELETE succeeds…
    vi.advanceTimersByTime(UNDO_MS);
    await until(() => expect(onForget).toHaveBeenCalledWith('marty rice'));

    // …and only now does the stale answer land.
    answerLate(junk);
    await quiesce();
    expect(labels(container)).toEqual(['Papa Dums', 'Coffee']);
  });
});
