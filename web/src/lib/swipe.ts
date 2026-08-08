/**
 * Pure geometry for the row swipe gesture — the same discipline as the drag
 * module's `targetIndex*` helpers: all the decision maths lives here as plain
 * functions so it can be unit-tested in isolation, while the stateful pointer
 * plumbing (capture, rAF-coalesced transform, spring-back) lives in ItemRow.
 *
 * Two directions, two meanings: swipe RIGHT sends an active row to the basket,
 * swipe LEFT deletes any row. A gesture is committed either by travelling far
 * enough (a fraction of the row width) or by a fast flick (velocity), and is
 * otherwise sprung back to rest.
 */

export type Axis = 'horizontal' | 'vertical' | null;
export type SwipeDir = 'left' | 'right';

/** Travel (px) before the gesture axis is decided. */
export const AXIS_LOCK_PX = 10;
/** Commit once travel reaches this fraction of the row width. */
export const DISTANCE_FRACTION = 0.35;
/** …or on a flick faster than this (px per ms), provided it cleared MIN_FLICK. */
export const VELOCITY_THRESHOLD = 0.5;
/** A flick must still travel at least this far so a jittery tap can't fire it. */
export const MIN_FLICK_PX = 24;
/**
 * Reduced motion: no slide to drag a threshold out of, so a single modest
 * horizontal travel triggers the action outright. Kept below the animated
 * distance commit so it feels deliberate but reachable.
 */
export const REDUCED_MOTION_TRIGGER_PX = 56;

/**
 * Decide the gesture axis once travel exceeds `threshold`. Returns null until
 * either component clears the lock distance, then 'horizontal' when the motion
 * is predominantly sideways (enter swipe mode) or 'vertical' (release to native
 * scroll). Ties (pure diagonal) resolve to vertical so scrolling always wins a
 * coin-flip — a missed swipe is cheaper than a hijacked scroll.
 */
export function axisLock(dx: number, dy: number, threshold: number): Axis {
  if (Math.abs(dx) < threshold && Math.abs(dy) < threshold) return null;
  return Math.abs(dx) > Math.abs(dy) ? 'horizontal' : 'vertical';
}

/**
 * Resolve a settled swipe to a direction, or null to spring back. Commits when
 * the row has travelled past `distanceFraction` of its width OR on a flick whose
 * speed clears `velocityThreshold` and whose travel cleared `minFlick`. The sign
 * of `dx` chooses the direction; a zero-width row can never commit on distance.
 */
export function swipeCommit(
  dx: number,
  velocity: number,
  rowWidth: number,
  opts: {
    distanceFraction: number;
    velocityThreshold: number;
    minFlick: number;
  },
): SwipeDir | null {
  const passedDistance = rowWidth > 0 && Math.abs(dx) >= rowWidth * opts.distanceFraction;
  const passedFlick =
    Math.abs(velocity) >= opts.velocityThreshold && Math.abs(dx) >= opts.minFlick;
  if (!passedDistance && !passedFlick) return null;
  return dx < 0 ? 'left' : 'right';
}

/**
 * Reveal progress [0,1] of the action affordance as the row translates — drives
 * the behind-row icon fading and scaling in as the commit threshold nears, so
 * the icon reads full-strength exactly when a release would commit.
 */
export function revealProgress(dx: number, rowWidth: number, distanceFraction: number): number {
  const commitAt = rowWidth * distanceFraction;
  if (commitAt <= 0) return 0;
  return Math.min(1, Math.abs(dx) / commitAt);
}

/**
 * The visual translate to apply for a given raw pointer delta. A swipe in an
 * ALLOWED direction tracks the pointer 1:1; a swipe in a BLOCKED direction (e.g.
 * swipe-right on a checked row, already in the basket) rubber-bands at a third
 * of the travel so it reads as a soft wall rather than a dead zone.
 */
export function resistedDelta(dx: number, rightAllowed: boolean, leftAllowed: boolean): number {
  const allowed = dx > 0 ? rightAllowed : leftAllowed;
  return allowed ? dx : dx / 3;
}
