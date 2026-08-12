import { fly, slide } from 'svelte/transition';
import { flip } from 'svelte/animate';
import type { AnimationConfig } from 'svelte/animate';
import type { TransitionConfig } from 'svelte/transition';

/**
 * Shared motion vocabulary for the app.
 *
 * The system is deliberately lopsided: a handful of things move, each of them
 * fast, close to the finger, and finished before you could look away. Nothing
 * travels across the viewport — a row leaving the active list and a row landing
 * in the basket are two rows in two lists, not one row flying between them, and
 * the honest reading of that is a departure and an arrival. (It was a flight
 * once. Measured at 2373px in 300ms it swept ~220°/s of visual angle, some
 * seven times past what smooth pursuit can track, so nobody ever saw the item
 * arrive — they saw a smear, on the single most repeated gesture in the app.)
 *
 * Two duration gates live here and they mean different things:
 *   `d()`        — travel: transforms, heights, positions. Zero under reduced
 *                  motion, because that is what "motion animation" means.
 *   `fadeDur()`  — opacity and colour. WCAG 2.3.3 exempts these, and a hard cut
 *                  is its own kind of jarring, so they are shortened, not cut.
 */

/**
 * Named durations (ms). Nothing here exceeds 400, and only one thing reaches
 * it. Read them as a system: an arrival is worth watching land, a departure is
 * already decided, so every exit is shorter than the entrance it mirrors.
 */
export const DUR = {
  /** A row landing in a list: fades up over an 8px rise. */
  enter: 250,
  /** A row leaving a list: fades and collapses its own height, in place. */
  exit: 200,
  /** A group or drawer unfolding. */
  expand: 250,
  /** A group or drawer folding shut. */
  collapse: 200,
  /** Strike-through draw + colour shift on check-off. Right under the finger,
   * so it has to be over before the thumb lifts. */
  check: 150,
  /** FLIP drift as a row changes slot. */
  flip: 250,
  /** Displaced rows parting around the live drag gap. Short + snappy: the gap
   * must track the pointer, not lag a third of a second behind it. */
  gap: 140,
  /** Box-shadow easing in as a row is picked up for a drag. Deliberately NOT
   * routed through `d()` at its call site: elevation is tone, not travel, and
   * app.css's reduced-motion block keeps box-shadow on its transition list at
   * 100ms for exactly that reason. See ItemRow's `.row.lifted`. */
  lift: 160,
  /** A swipe rubber-banding home after an uncommitted drag. */
  swipeBack: 200,
  /** A swipe carrying the row off the edge once it has committed. */
  swipeCommit: 250,
  /** Toast / banner arriving. Slower in than out: it has something to say. */
  toastIn: 300,
  /** Toast / banner leaving. */
  toastOut: 200,
  /** One-shot accent glow as a ring arrival lands. Colour, not travel. */
  glow: 350,
  /** Clearing the basket — the one expressive moment of the shop, and the only
   * thing in the app allowed 400ms. It is the end of the trip, it happens once,
   * and it is the single gesture where watching the pile go is the point. */
  clear: 400,
} as const;

/** The four control points of each named curve, in CSS order. */
const CURVE = {
  /** Decelerate hard: an arrival covers its ground early and settles. */
  enter: [0, 0, 0, 1],
  /** Accelerate: a departure starts where it stood and is gone. */
  exit: [0.3, 0, 1, 1],
  /** Emphasised decelerate — the drawer opens with some weight behind it. */
  expand: [0.05, 0.7, 0.1, 1],
  /** Emphasised accelerate — and shuts without ceremony. */
  collapse: [0.3, 0, 0.8, 0.15],
  /** Standard: the strike-through and the drag gap, both of which are tracking
   * something the hand is already doing. */
  check: [0.2, 0, 0, 1],
  /** Same curve, named for the other place it is used. */
  gap: [0.2, 0, 0, 1],
  /** Rubber-band home: ease-out-quint character, so the row snaps back and
   * eases the last few pixels rather than braking all the way. */
  swipeBack: [0.22, 1, 0.36, 1],
  /** Off the edge: the same family, a shade less abrupt at the start. */
  swipeCommit: [0.33, 1, 0.68, 1],
  /** The basket clearing: a long, slightly weighted farewell. */
  clear: [0.4, 0.14, 0.3, 1],
} as const;

type CurveName = keyof typeof CURVE;
type Easing = (t: number) => number;

/**
 * A cubic-bezier easing as a function of `t`, matching CSS's `cubic-bezier()`
 * exactly. Svelte transitions take an easing function and stylesheets take a
 * curve literal, so both have to be sayable from one definition — otherwise the
 * JS-driven half of the motion and the CSS half drift a curve apart and nobody
 * notices until the two play side by side.
 */
export function bezier(x1: number, y1: number, x2: number, y2: number): Easing {
  const a = (p1: number, p2: number) => 1 - 3 * p2 + 3 * p1;
  const b = (p1: number, p2: number) => 3 * p2 - 6 * p1;
  const c = (p1: number) => 3 * p1;
  const calc = (t: number, p1: number, p2: number) =>
    ((a(p1, p2) * t + b(p1, p2)) * t + c(p1)) * t;
  const slope = (t: number, p1: number, p2: number) =>
    3 * a(p1, p2) * t * t + 2 * b(p1, p2) * t + c(p1);

  return (t: number) => {
    if (t <= 0) return 0;
    if (t >= 1) return 1;
    // Newton-Raphson from t itself, which is close enough for these curves to
    // converge in a couple of steps. A flat segment gives a zero derivative and
    // no correction to make, so bail to bisection rather than dividing by it.
    let guess = t;
    for (let i = 0; i < 8; i += 1) {
      const err = calc(guess, x1, x2) - t;
      if (Math.abs(err) < 1e-6) return calc(guess, y1, y2);
      const dx = slope(guess, x1, x2);
      if (Math.abs(dx) < 1e-6) break;
      guess -= err / dx;
    }
    let lo = 0;
    let hi = 1;
    guess = t;
    for (let i = 0; i < 24; i += 1) {
      const x = calc(guess, x1, x2);
      if (Math.abs(x - t) < 1e-6) break;
      if (x > t) hi = guess;
      else lo = guess;
      guess = (lo + hi) / 2;
    }
    return calc(guess, y1, y2);
  };
}

/** The curves as easing functions, for svelte transitions and animations. */
export const EASE = Object.fromEntries(
  Object.entries(CURVE).map(([name, c]) => [name, bezier(c[0], c[1], c[2], c[3])]),
) as Record<CurveName, Easing>;

/** The same curves as CSS literals, for inline styles and stylesheets. */
export const EASE_CSS = Object.fromEntries(
  Object.entries(CURVE).map(([name, c]) => [name, `cubic-bezier(${c.join(', ')})`]),
) as Record<CurveName, string>;

/** Per-index delay step for a staggered cascade (ms). */
export const STAGGER = 30;
/**
 * Index past which the cascade stops stepping. Five rows is enough to say "these
 * arrived together"; beyond that the stagger is only latency, and an uncapped
 * 45ms step left the twelfth row of a ring drop waiting half a second to exist.
 */
export const STAGGER_CAP = 4;

/** Delay for the row at `index` in a staggered batch (ms), capped and gated. */
export function staggerDelay(index: number): number {
  return d(Math.min(Math.max(index, 0), STAGGER_CAP) * STAGGER);
}

/** Spring for the check-off squash. Firmer and better damped than it was: one
 * quick dip and done, with no visible bounce to sit through. */
export const SQUASH_SPRING = { stiffness: 0.4, damping: 0.75 } as const;

/** How far the chip squashes on check-off. Shallow on purpose — the strike and
 * the colour carry the message; this is only the physical acknowledgement. */
export const SQUASH_DIP = 0.94;

/** Spring for the recents-tray press-scale. Firm, near-critically damped. */
export const PRESS_SPRING = { stiffness: 0.45, damping: 0.85 } as const;

/** How far a pressed pill dips. Barely there, and entirely under the finger. */
export const PRESS_DIP = 0.96;

/** Longest a substituted fade runs under reduced motion (ms). Mirrored by the
 * `prefers-reduced-motion` block in app.css, which gates the CSS-side half. */
export const REDUCED_FADE = 100;

/**
 * Live read of the user's reduced-motion preference.
 *
 * Read at call time, not cached at module load, so a session that changes the
 * OS setting gets the new answer from the next call. It is NOT reactive: an
 * inline `transition:` string or a `--gap-dur` custom property that was written
 * during a render keeps the duration it was given until that node renders
 * again. In practice the preference changes far more rarely than the list does.
 */
export function reducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Gate a TRAVEL duration — transform, height, position. 0 under reduced motion. */
export function d(ms: number): number {
  return reducedMotion() ? 0 : ms;
}

/**
 * Gate a FADE duration — opacity or colour. Shortened under reduced motion, not
 * removed: WCAG 2.3.3 explicitly excludes colour and opacity from the motion it
 * asks to be suppressed, and a row that blinks in and out between two frames
 * gives no sign that anything happened, which is its own accessibility problem.
 */
export function fadeDur(ms: number): number {
  return reducedMotion() ? Math.min(ms, REDUCED_FADE) : ms;
}

/**
 * Fade a node in or out over a short rise. The house entrance/exit for anything
 * that is not a list row: toasts, banners, the settings sheet.
 *
 * Under reduced motion the rise goes and the fade stays, which is the whole
 * substitution rule in one function.
 */
export function rise(
  node: Element,
  params: { y?: number; duration: number; easing?: Easing; delay?: number },
): TransitionConfig {
  if (reducedMotion()) {
    return { duration: fadeDur(params.duration), css: (t: number) => `opacity: ${t}` };
  }
  return fly(node, {
    y: params.y ?? 8,
    duration: params.duration,
    delay: params.delay ?? 0,
    easing: params.easing ?? EASE.enter,
  });
}

/**
 * Open / close a disclosure: an aisle, the basket drawer, a settings section.
 *
 * `slide` carrying the house expand/collapse pair, as one function rather than
 * eleven hand-written copies of the same two lines — the same consolidation
 * `rise` did for `fly`. Written out at each call site, the pair drifted: it is
 * two durations and two curves, and a hurried edit only ever touches the one in
 * front of it.
 *
 * `d()` gates both, so under reduced motion the fold happens in a single frame
 * — a height change is travel, and there is nothing here worth substituting a
 * fade for; the content is either there or it is not.
 */
export function unfold(node: Element): TransitionConfig {
  return slide(node, { duration: d(DUR.expand), easing: EASE.expand });
}

/** The closing half of `unfold`. Shorter, like every exit in the table. */
export function fold(node: Element): TransitionConfig {
  return slide(node, { duration: d(DUR.collapse), easing: EASE.collapse });
}

/**
 * Entrance for a list row. Ring arrivals cascade by position so a batch pushed
 * in over SSE reads as one delivery rather than five unrelated pops; everything
 * else — your own add, an item coming back out of the basket — simply lands.
 */
export function arriveRow(
  node: Element,
  params: { source?: string | null; index?: number; suppress?: boolean },
): TransitionConfig {
  // Suppressed: a cross-aisle drag ghost is placing this row, so it has no
  // entrance of its own to play — two settles on one row read as a stutter.
  if (params.suppress) return { duration: 0 };
  return rise(node, {
    y: 8,
    duration: DUR.enter,
    easing: EASE.enter,
    delay: params.source === 'ring' ? staggerDelay(params.index ?? 0) : 0,
  });
}

/**
 * Exit for a list row: it fades where it stands while its own height closes up,
 * so the thing you actually have to see — the gap shutting and the rows below
 * stepping up — is the motion. Nothing travels.
 *
 * `duration`/`easing`/`delay` are overridden for exactly one gesture, clearing
 * the basket, where the pile leaves as a staggered cascade.
 *
 * Under reduced motion only the fade survives: the height snaps shut in a
 * single frame, so no row is ever seen moving.
 */
export function leaveRow(
  node: Element,
  params: { duration?: number; easing?: Easing; delay?: number; suppress?: boolean } = {},
): TransitionConfig {
  if (params.suppress) return { duration: 0 };
  const duration = params.duration ?? DUR.exit;
  if (reducedMotion()) {
    return { duration: fadeDur(duration), css: (t: number) => `opacity: ${t}` };
  }
  const style = getComputedStyle(node);
  const px = (value: string) => parseFloat(value) || 0;
  const height = px(style.height);
  const padTop = px(style.paddingTop);
  const padBottom = px(style.paddingBottom);
  const marginTop = px(style.marginTop);
  const marginBottom = px(style.marginBottom);
  const borderTop = px(style.borderTopWidth);
  const borderBottom = px(style.borderBottomWidth);
  return {
    delay: params.delay ?? 0,
    duration,
    easing: params.easing ?? EASE.exit,
    // Opacity runs ahead of the collapse (fully faded by 60% of the way through)
    // so the row is gone before its box is, and the closing gap is what the eye
    // is left following rather than a ghost shrinking to a line.
    css: (t: number) =>
      'overflow: hidden;' +
      `opacity: ${Math.max(0, Math.min(1, t / 0.6))};` +
      `height: ${t * height}px;` +
      `padding-top: ${t * padTop}px;` +
      `padding-bottom: ${t * padBottom}px;` +
      `margin-top: ${t * marginTop}px;` +
      `margin-bottom: ${t * marginBottom}px;` +
      `border-top-width: ${t * borderTop}px;` +
      `border-bottom-width: ${t * borderBottom}px;` +
      'min-height: 0;',
  };
}

/**
 * FLIP that refuses to animate a box it could not measure.
 *
 * The recents tray keeps its overflow pills in the each-block and hides them
 * with `display: none`. That choice is an accessibility one — a shortcut you
 * cannot see must be out of the tab order and out of the screen reader's list,
 * which `visibility: hidden` would not give and a plain visual trick would not
 * either — and keeping them mounted happens to be what lets a re-measure read
 * the whole set. What it costs is this: a `display: none` node measures all
 * zeros, and svelte's stock flip happily divides by that. Hidden pills emitted
 * `translate(NaNpx, NaNpx) scale(Infinity, Infinity)`, and a pill promoted INTO
 * view was flipped from a zero rect — 350px of travel and a scale from a single
 * point, on a pill that had simply appeared. There is no "from" to move from in
 * either case, so there is nothing to animate.
 *
 * `maxTravel` caps how far it is willing to fly something, for the case where
 * the geometry is real but the movement is not one anybody made: the recents
 * tray re-wraps when a pill is picked, and a pill landing on the previous row
 * crosses the whole tray to get there. The measurement and the cap it argues
 * for live at the tray's own `PILL_MAX_TRAVEL`. Past the cap the pill simply
 * appears in its new slot.
 */
export function settleFlip(
  node: Element,
  dims: { from: DOMRect; to: DOMRect },
  params: { duration: number; maxTravel?: number },
): AnimationConfig {
  const { from, to } = dims;
  if (!from || !to || !from.width || !from.height || !to.width || !to.height) {
    return { duration: 0 };
  }
  const travel = Math.hypot(from.left - to.left, from.top - to.top);
  if (params.maxTravel !== undefined && travel > params.maxTravel) return { duration: 0 };
  return flip(node, dims, params);
}
