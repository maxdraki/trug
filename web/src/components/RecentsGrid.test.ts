import { render, screen } from '@testing-library/svelte';
import { tick } from 'svelte';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import RecentsGrid from './RecentsGrid.svelte';
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

/** Resolves `top()` and lets the component's $effect (and its measurement) settle. */
async function renderTray(entries: CatalogEntry[], activeNames = new Set<string>()) {
  const onPick = vi.fn();
  const top = () => Promise.resolve(entries);
  const result = render(RecentsGrid, { top, activeNames, onPick });
  await vi.waitFor(() => expect(result.container.querySelector(PILL)).toBeTruthy());
  await tick();
  /** Swaps in a new set of already-on-the-list names, as the store does mid-shop. */
  const setActive = (names: Set<string>) => result.rerender({ top, activeNames: names, onPick });
  return { ...result, onPick, setActive };
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
