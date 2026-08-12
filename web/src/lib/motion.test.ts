import { describe, it, expect, afterEach, vi } from 'vitest';
import type { TransitionConfig } from 'svelte/transition';
import {
  reducedMotion,
  d,
  fadeDur,
  REDUCED_FADE,
  DUR,
  EASE,
  EASE_CSS,
  STAGGER,
  STAGGER_CAP,
  staggerDelay,
  SQUASH_SPRING,
  SQUASH_DIP,
  PRESS_SPRING,
  PRESS_DIP,
  bezier,
  arriveRow,
  leaveRow,
  rise,
  settleFlip,
} from './motion';

/** Install a matchMedia stub whose reduce query returns `reduce`. */
function mockMatchMedia(reduce: boolean) {
  const mql = { matches: reduce } as MediaQueryList;
  const fn = vi.fn((q: string) =>
    /prefers-reduced-motion:\s*reduce/.test(q) ? mql : ({ matches: false } as MediaQueryList),
  );
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: fn });
  return fn;
}

afterEach(() => {
  // Remove the stub so unrelated suites see the jsdom default again.
  // @ts-expect-error - allow deleting the patched property
  delete window.matchMedia;
});

describe('reducedMotion', () => {
  it('is true when the reduce media query matches', () => {
    mockMatchMedia(true);
    expect(reducedMotion()).toBe(true);
  });

  it('is false when the reduce media query does not match', () => {
    mockMatchMedia(false);
    expect(reducedMotion()).toBe(false);
  });

  it('is read live, not cached at module load', () => {
    mockMatchMedia(false);
    expect(reducedMotion()).toBe(false);
    mockMatchMedia(true);
    expect(reducedMotion()).toBe(true);
  });

  it('is false when matchMedia is unavailable', () => {
    // @ts-expect-error - simulate an environment without matchMedia
    delete window.matchMedia;
    expect(reducedMotion()).toBe(false);
  });
});

describe('d()', () => {
  it('returns 0 when reduced motion is on', () => {
    mockMatchMedia(true);
    expect(d(300)).toBe(0);
    expect(d(DUR.check)).toBe(0);
  });

  it('passes the duration through when reduced motion is off', () => {
    mockMatchMedia(false);
    expect(d(300)).toBe(300);
    expect(d(DUR.enter)).toBe(DUR.enter);
  });
});

describe('fadeDur()', () => {
  it('keeps a fade alive — shortened, not stripped — under reduced motion', () => {
    // WCAG 2.3.3 exempts opacity and colour from "motion animation", and a hard
    // swap of one row for another is its own kind of jarring. Substituting a
    // short fade is what WebKit's guidance asks for.
    mockMatchMedia(true);
    expect(fadeDur(DUR.enter)).toBe(REDUCED_FADE);
  });

  it('never lengthens a fade that was already shorter than the substitute', () => {
    mockMatchMedia(true);
    expect(fadeDur(60)).toBe(60);
  });

  it('passes the duration through when reduced motion is off', () => {
    mockMatchMedia(false);
    expect(fadeDur(DUR.toastIn)).toBe(DUR.toastIn);
  });
});

describe('duration tokens', () => {
  it('spends the 400ms ceiling only on clearing the basket', () => {
    // The one expressive moment of the shop: the pile you finished with leaving
    // in a cascade. Everything else is feedback, and feedback that takes 400ms
    // sits between a tap and its result. (This subsumes the ceiling itself:
    // clearing is at it and nothing else comes within 50ms of it.)
    for (const [name, ms] of Object.entries(DUR)) {
      expect(ms, `${name} must be a real duration`).toBeGreaterThan(0);
      if (name === 'clear') continue;
      expect(ms, `${name} is feedback, not an occasion`).toBeLessThanOrEqual(350);
    }
    expect(DUR.clear).toBe(400);
  });

  it('exits faster than it enters, in every pairing', () => {
    // Asymmetry is the whole point: something arriving is worth watching land,
    // something leaving is already decided.
    expect(DUR.exit).toBeLessThan(DUR.enter);
    expect(DUR.collapse).toBeLessThan(DUR.expand);
    expect(DUR.toastOut).toBeLessThan(DUR.toastIn);
  });

  it('tunes both springs for one dip and no bounce to sit through', () => {
    // These two are the only springs in the app and they are both under the
    // finger: a chip squashing on check-off, a recents pill dipping on press.
    // The earlier tuning (a 0.9 dip on a springier curve) read as a wobble on
    // the most-repeated gesture there is, so what matters is the pair of
    // properties that fixed it, not that the numbers are numbers.
    for (const [name, spring] of Object.entries({ SQUASH_SPRING, PRESS_SPRING })) {
      // Firm enough to be over before the thumb lifts...
      expect(spring.stiffness, `${name} stiffness`).toBeGreaterThanOrEqual(0.4);
      // ...and damped hard enough that it settles without a visible overshoot.
      expect(spring.damping, `${name} damping`).toBeGreaterThanOrEqual(0.7);
      expect(spring.damping, `${name} damping`).toBeLessThanOrEqual(1);
    }
  });

  it('keeps both press dips shallow — an acknowledgement, not a squash', () => {
    // The strike-through and the colour carry the check-off; the chip only has
    // to confirm the tap landed. The old 0.9 dip was a visible deformation.
    for (const [name, dip] of Object.entries({ SQUASH_DIP, PRESS_DIP })) {
      expect(dip, `${name}`).toBeGreaterThan(0.9);
      expect(dip, `${name}`).toBeLessThan(1);
    }
    // And the one directly under a moving thumb is the subtler of the two.
    expect(PRESS_DIP).toBeGreaterThan(SQUASH_DIP);
  });
});

describe('staggerDelay()', () => {
  it('steps each index by the stagger', () => {
    mockMatchMedia(false);
    expect(staggerDelay(0)).toBe(0);
    expect(staggerDelay(2)).toBe(2 * STAGGER);
  });

  it('caps the cascade so a long batch never waits on its own tail', () => {
    // Uncapped, a twelve-item ring drop left the last row waiting half a second
    // to appear. Past a few steps the cascade has already said "these arrived
    // together"; the rest is just latency.
    mockMatchMedia(false);
    expect(staggerDelay(4)).toBe(STAGGER_CAP * STAGGER);
    expect(staggerDelay(40)).toBe(STAGGER_CAP * STAGGER);
    // The budget the cap exists to keep, in absolute terms rather than as the
    // product of the two constants that set it: no row in a batch may wait
    // longer than a beat before it exists.
    expect(staggerDelay(40)).toBeLessThanOrEqual(150);
  });

  it('is instant under reduced motion', () => {
    mockMatchMedia(true);
    expect(staggerDelay(3)).toBe(0);
  });
});

describe('bezier()', () => {
  it('pins both ends of the curve', () => {
    const e = bezier(0.3, 0, 1, 1);
    expect(e(0)).toBeCloseTo(0, 5);
    expect(e(1)).toBeCloseTo(1, 5);
  });

  it('reproduces linear when the handles are on the diagonal', () => {
    const e = bezier(0, 0, 1, 1);
    for (const t of [0.1, 0.25, 0.5, 0.75, 0.9]) expect(e(t)).toBeCloseTo(t, 3);
  });

  it('rises without ever going backwards', () => {
    const e = EASE.expand;
    let prev = -Infinity;
    for (let i = 0; i <= 50; i += 1) {
      const v = e(i / 50);
      expect(v).toBeGreaterThanOrEqual(prev - 1e-9);
      prev = v;
    }
  });
});

describe('easing tokens', () => {
  it('decelerates an arrival and accelerates a departure', () => {
    // Something arriving does most of its travel early and settles; something
    // leaving starts slow and is gone. Half-way through, that is the whole
    // difference, and it is the difference between the two curves.
    expect(EASE.enter(0.5)).toBeGreaterThan(0.5);
    expect(EASE.exit(0.5)).toBeLessThan(0.5);
    expect(EASE.expand(0.5)).toBeGreaterThan(0.5);
    expect(EASE.collapse(0.5)).toBeLessThan(0.5);
  });

  it('publishes every curve as a CSS string that traces the same path', () => {
    // Half the motion in the app is CSS transitions and half is JS-driven, so
    // the two have to be sayable from one definition or they drift a curve
    // apart and nobody notices until they play side by side. Restating the
    // literals here would only restate the source; sampling both readings of
    // every curve is what actually catches a drift.
    expect(Object.keys(EASE_CSS).sort()).toEqual(Object.keys(EASE).sort());
    for (const [name, css] of Object.entries(EASE_CSS)) {
      const handles = /^cubic-bezier\(([^)]*)\)$/.exec(css);
      expect(handles, `${name} is not a cubic-bezier() literal: ${css}`).not.toBeNull();
      const [x1, y1, x2, y2] = handles![1].split(',').map((n) => Number(n.trim()));
      const fromCss = bezier(x1, y1, x2, y2);
      for (const t of [0.1, 0.25, 0.5, 0.75, 0.9]) {
        expect(fromCss(t), `${name} at t=${t}`).toBeCloseTo(EASE[name as keyof typeof EASE](t), 6);
      }
    }
  });
});

/** The transform an entrance/exit applies at progress `t`, or '' if it has none. */
function transformAt(cfg: TransitionConfig, t: number): string {
  const css = cfg.css!(t, 1 - t);
  return /transform:\s*([^;]*)/.exec(css)?.[1] ?? '';
}

describe('arriveRow', () => {
  it('rises a row into place, whatever put it there', () => {
    mockMatchMedia(false);
    const node = document.createElement('div');
    const cfg = arriveRow(node, { source: 'local', index: 2 });
    expect(cfg.duration).toBe(DUR.enter);
    expect(cfg.delay).toBe(0);
    expect(transformAt(cfg, 0)).toContain('translate');
  });

  it('cascades ring arrivals by their position', () => {
    mockMatchMedia(false);
    const node = document.createElement('div');
    const cfg = arriveRow(node, { source: 'ring', index: 3 });
    expect(cfg.duration).toBe(DUR.enter);
    expect(cfg.delay).toBe(3 * STAGGER);
  });

  it('substitutes a fade under reduced motion rather than nothing at all', () => {
    // Only the travel is a motion animation. Stripping the fade as well makes
    // a row appear between two frames with no acknowledgement that anything
    // happened — the row is simply there, and you have to re-read the list.
    mockMatchMedia(true);
    const node = document.createElement('div');
    const cfg = arriveRow(node, { source: 'ring', index: 3 });
    expect(cfg.duration).toBe(REDUCED_FADE);
    expect(cfg.delay ?? 0).toBe(0);
    expect(transformAt(cfg, 0.5)).toBe('');
    expect(cfg.css!(0.5, 0.5)).toContain('opacity');
  });

  it('sits out entirely while a drag ghost is flying the row in', () => {
    mockMatchMedia(false);
    const node = document.createElement('div');
    expect(arriveRow(node, { source: 'local', suppress: true }).duration).toBe(0);
  });
});

describe('leaveRow', () => {
  /** A row with a real box, the way a laid-out browser reports one. */
  function row(height = 44): HTMLElement {
    const el = document.createElement('div');
    el.style.height = `${height}px`;
    document.body.appendChild(el);
    return el;
  }

  it('fades and collapses in place, taking the gap with it', () => {
    // The row does not travel to the basket: it is two rows in two lists, and
    // the honest reading of that is one leaving and one arriving. What the eye
    // must catch is the gap closing, which is the height collapse.
    mockMatchMedia(false);
    const cfg = leaveRow(row(44));
    expect(cfg.duration).toBe(DUR.exit);
    const css = cfg.css!(0.5, 0.5);
    expect(css).toContain('height: 22px');
    expect(css).not.toContain('translate');
    // The fade leads the collapse, so the row is gone before its box is and
    // what the eye follows out is the gap shutting, not a ghost shrinking.
    expect(Number(/opacity: ([\d.]+)/.exec(css)![1])).toBeGreaterThan(0.5);
    expect(cfg.css!(0, 1)).toContain('opacity: 0;');
  });

  it('takes a longer, staggered exit when a whole basket is cleared', () => {
    mockMatchMedia(false);
    const cfg = leaveRow(row(), { duration: DUR.clear, easing: EASE.clear, delay: staggerDelay(2) });
    expect(cfg.duration).toBe(DUR.clear);
    expect(cfg.delay).toBe(2 * STAGGER);
  });

  it('drops the height collapse but keeps the fade under reduced motion', () => {
    mockMatchMedia(true);
    const cfg = leaveRow(row(44));
    expect(cfg.duration).toBe(REDUCED_FADE);
    const css = cfg.css!(0.5, 0.5);
    expect(css).toContain('opacity');
    expect(css).not.toContain('height');
  });

  it('sits out entirely while a drag ghost is flying the row out', () => {
    mockMatchMedia(false);
    expect(leaveRow(row(), { suppress: true }).duration).toBe(0);
  });
});

describe('settleFlip', () => {
  const rect = (width: number, height: number, left = 0, top = 0) =>
    ({ width, height, left, top, right: left + width, bottom: top + height }) as DOMRect;

  it('refuses to animate from a box that measured nothing', () => {
    // A clipped recents pill is `display: none`, so it measures all zeros. The
    // stock flip divides by that: hidden pills emitted `scale(Infinity)`, and a
    // pill promoted into view was flown in from a point ~350px away.
    const node = document.createElement('button');
    expect(settleFlip(node, { from: rect(0, 0), to: rect(80, 34) }, { duration: 250 }).duration).toBe(0);
    expect(settleFlip(node, { from: rect(80, 34), to: rect(0, 0) }, { duration: 250 }).duration).toBe(0);
  });

  it('refuses to fly something further than the eye can follow', () => {
    // A recents pill re-wrapping onto the previous row crosses the whole tray
    // to get there. Nothing moved — the text reflowed — and 318px in 250ms is
    // back over the tracking ceiling the check-off flight was deleted for.
    const node = document.createElement('button');
    const dims = { from: rect(80, 34, 600, 42), to: rect(80, 34, 8, 0) };
    expect(settleFlip(node, dims, { duration: 250, maxTravel: 120 }).duration).toBe(0);
    // ...and still slides one that only stepped along its own row.
    const near = { from: rect(80, 34, 100, 0), to: rect(80, 34, 8, 0) };
    expect(settleFlip(node, near, { duration: 250, maxTravel: 120 }).duration).toBe(250);
  });

  it('animates a move between two real boxes', () => {
    const node = document.createElement('button');
    const cfg = settleFlip(node, { from: rect(80, 34, 0, 0), to: rect(80, 34, 0, 42) }, { duration: 250 });
    expect(cfg.duration).toBe(250);
  });
});

describe('rise()', () => {
  // Its reduced-motion half is covered where it is actually reached — through
  // arriveRow above, which is the only caller that has a substitution rule to
  // get wrong. Repeating it here only re-ran the same branch.
  it('flies in from an offset when motion is welcome', () => {
    mockMatchMedia(false);
    const cfg = rise(document.createElement('div'), { y: 12, duration: DUR.toastIn });
    expect(cfg.duration).toBe(DUR.toastIn);
    expect(transformAt(cfg, 0)).toContain('translate');
  });
});
