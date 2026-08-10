import { fly } from 'svelte/transition';
import { cubicOut } from 'svelte/easing';
import type { TransitionConfig } from 'svelte/transition';

/**
 * Shared motion vocabulary for the app. Every duration in the UI is routed
 * through `d()` so a single reduced-motion check collapses the whole animation
 * set to instant while leaving state changes (and haptics) intact.
 *
 * All durations are kept within a 350ms budget — long enough to read as
 * intentional, short enough to never sit between a tap and its result.
 */

/** Named durations (ms). Keep every value ≤ 350. */
export const DUR = {
  /** Crossfade of a chip/suggestion into its aisle row. */
  add: 300,
  /** Group height easing open as its first row lands. */
  slide: 260,
  /** Strike-through draw + squash on check-off. */
  check: 260,
  /** Fly in/out for ring arrivals and cleared items. */
  fly: 300,
  /** FLIP drift as an item moves between piles. */
  flip: 300,
  /** Displaced rows parting around the live drag gap. Short + snappy: the gap
   * must track the pointer, not lag a third of a second behind it. */
  gap: 140,
  /** One-shot accent glow as a ring arrival lands. */
  glow: 350,
} as const;

/** Per-index delay step for staggered entrances/exits (ms). */
export const STAGGER = 45;

/** Spring for the check-off squash (svelte/motion `Spring`). Quick settle, a
 * single barely-perceptible bounce — precision over bounce. */
export const SQUASH_SPRING = { stiffness: 0.32, damping: 0.5 } as const;

/** Spring for recents-tray press-scale. Firm, near-critical damping. */
export const PRESS_SPRING = { stiffness: 0.4, damping: 0.6 } as const;

/**
 * Live read of the user's reduced-motion preference. Deliberately not cached at
 * module load so the value tracks OS/browser changes within a session (and so
 * tests can swap the matchMedia stub between assertions).
 */
export function reducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Gate a duration: 0 when reduced motion is requested, otherwise `ms`. */
export function d(ms: number): number {
  return reducedMotion() ? 0 : ms;
}

/**
 * Normalised crossfade key for an item/catalogue name, matching the store's
 * own normalisation so a suggestion chip and its resulting aisle row pair up.
 */
export { normaliseName as keyOf } from './text';

/**
 * Hand-rolled crossfade pair for the add/check gestures: a chip (AddBar
 * suggestion, recents tile, or an aisle row headed for the basket) `send`s to
 * its matching counterpart which `receive`s it. Unlike svelte's stock
 * crossfade the flight path has a slight upward arc and a barely-perceptible
 * overshoot on landing — a toss into the basket, kept precise. Durations are
 * gated through `d()` so reduced motion collapses to an instant swap.
 */
const toReceive = new Map<string, Element>();
const toSend = new Map<string, Element>();

/** backOut with a small overshoot constant (~1% past target, not the stock 10%). */
function subtleBack(t: number): number {
  const s = 1.15;
  const u = t - 1;
  return 1 + u * u * ((s + 1) * u + s);
}

function flight(intro: boolean) {
  return (node: Element, params: { key: string; suppress?: boolean }): (() => TransitionConfig) => {
    // A drag-move settles this row itself (hand-rolled FLIP or a flying ghost),
    // so the crossfade flight must sit out or the row double-animates.
    if (params.suppress) return () => ({ duration: 0 });
    const items = intro ? toReceive : toSend;
    const counterparts = intro ? toSend : toReceive;
    items.set(params.key, node);
    return () => {
      items.delete(params.key);
      const other = counterparts.get(params.key);
      if (!other) {
        // No paired element: quiet fade/scale, same as the old fallback.
        return {
          duration: d(DUR.add),
          easing: cubicOut,
          css: (t: number) => `opacity: ${t}; transform: scale(${0.97 + 0.03 * t})`,
        };
      }
      counterparts.delete(params.key);
      const from = other.getBoundingClientRect();
      const to = node.getBoundingClientRect();
      const dx = from.left - to.left;
      const dy = from.top - to.top;
      const dist = Math.hypot(dx, dy);
      const style = getComputedStyle(node);
      const transform = style.transform === 'none' ? '' : style.transform;
      // Arc height scales with distance, capped so short hops stay near-linear.
      const arc = Math.min(10, dist / 14);
      return {
        duration: d(Math.min(DUR.add, 120 + dist / 4)),
        css: (t: number) => {
          const e = subtleBack(t);
          const lift = Math.sin(Math.min(1, Math.max(0, t)) * Math.PI) * arc;
          return (
            `transform: ${transform} translate(${(1 - e) * dx}px, ${(1 - e) * dy - lift}px) ` +
            `scale(${0.95 + 0.05 * e}); opacity: ${t}`
          );
        },
      };
    };
  };
}

export const sendItem = flight(false);
export const receiveItem = flight(true);

/**
 * Entrance dispatcher for an aisle/checked row. Ring arrivals (pushed in over
 * SSE) cascade in with a staggered `fly` keyed on their position; locally-added
 * rows keep the crossfade `receive` so the add-bar/recents chip flight pairs up.
 */
export function arriveRow(
  node: Element,
  params: { key: string; source?: string | null; index?: number; suppress?: boolean },
): TransitionConfig | (() => TransitionConfig) {
  // Suppressed: a cross-aisle ghost is flying this row in; no entrance of its own.
  if (params.suppress) return { duration: 0 };
  if (params.source === 'ring') {
    return fly(node, {
      y: 8,
      duration: d(DUR.fly),
      delay: d((params.index ?? 0) * STAGGER),
      easing: cubicOut,
    });
  }
  return receiveItem(node, { key: params.key });
}
