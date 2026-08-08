import { describe, it, expect } from 'vitest';
import { axisLock, swipeCommit, revealProgress, resistedDelta } from './swipe';

/**
 * Mirrors drag.test.ts: the swipe controller shares these pure functions between
 * the live follow (every pointer move) and the release commit, so what the row
 * previews as it slides is provably what a release does.
 */

describe('axisLock', () => {
  const T = 10;

  it('stays undecided until travel clears the threshold', () => {
    expect(axisLock(0, 0, T)).toBe(null);
    expect(axisLock(9, 3, T)).toBe(null);
    expect(axisLock(-4, -9, T)).toBe(null);
  });

  it('locks horizontal for predominantly sideways travel', () => {
    expect(axisLock(12, 3, T)).toBe('horizontal');
    expect(axisLock(-40, 10, T)).toBe('horizontal');
  });

  it('locks vertical for predominantly up/down travel', () => {
    expect(axisLock(3, 12, T)).toBe('vertical');
    expect(axisLock(-6, -20, T)).toBe('vertical');
  });

  it('resolves a pure diagonal to vertical so scrolling wins the tie', () => {
    expect(axisLock(15, 15, T)).toBe('vertical');
  });

  it('locks on either axis clearing the threshold alone', () => {
    expect(axisLock(11, 0, T)).toBe('horizontal');
    expect(axisLock(0, 11, T)).toBe('vertical');
  });
});

describe('swipeCommit', () => {
  const opts = { distanceFraction: 0.35, velocityThreshold: 0.5, minFlick: 24 };
  const W = 300; // 35% => 105px commit distance

  it('springs back (null) below both distance and flick thresholds', () => {
    expect(swipeCommit(40, 0.1, W, opts)).toBe(null);
    expect(swipeCommit(-80, -0.2, W, opts)).toBe(null);
  });

  it('commits by distance once past the fraction of the row width', () => {
    expect(swipeCommit(110, 0, W, opts)).toBe('right');
    expect(swipeCommit(-110, 0, W, opts)).toBe('left');
    expect(swipeCommit(104, 0, W, opts)).toBe(null); // just under 105
    expect(swipeCommit(105, 0, W, opts)).toBe('right'); // exactly at
  });

  it('commits by a fast flick even when short, if it cleared the min flick', () => {
    expect(swipeCommit(30, 0.8, W, opts)).toBe('right'); // fast + past 24px
    expect(swipeCommit(-30, -0.8, W, opts)).toBe('left');
  });

  it('ignores a fast flick that has not travelled the minimum', () => {
    expect(swipeCommit(20, 2, W, opts)).toBe(null); // fast but under 24px
  });

  it('ignores a slow flick that has not travelled far enough', () => {
    expect(swipeCommit(60, 0.3, W, opts)).toBe(null);
  });

  it('never commits on distance for a zero-width row', () => {
    expect(swipeCommit(500, 0, 0, opts)).toBe(null);
    // …but a flick still can, since it does not depend on width.
    expect(swipeCommit(500, 1, 0, opts)).toBe('right');
  });
});

describe('revealProgress', () => {
  it('ramps 0→1 across the commit distance and clamps at 1', () => {
    expect(revealProgress(0, 300, 0.35)).toBe(0);
    expect(revealProgress(105, 300, 0.35)).toBeCloseTo(1);
    expect(revealProgress(52.5, 300, 0.35)).toBeCloseTo(0.5);
    expect(revealProgress(400, 300, 0.35)).toBe(1); // clamped
  });

  it('is symmetric in direction (uses absolute travel)', () => {
    expect(revealProgress(-52.5, 300, 0.35)).toBeCloseTo(0.5);
  });

  it('is 0 for a zero-width row (no commit target)', () => {
    expect(revealProgress(50, 0, 0.35)).toBe(0);
  });
});

describe('resistedDelta', () => {
  it('tracks the pointer 1:1 in an allowed direction', () => {
    expect(resistedDelta(80, true, true)).toBe(80);
    expect(resistedDelta(-80, true, true)).toBe(-80);
  });

  it('rubber-bands to a third in a blocked direction', () => {
    // Swipe-right blocked (checked row already in basket): soft wall.
    expect(resistedDelta(90, false, true)).toBe(30);
    // Swipe-left blocked: same on the other side.
    expect(resistedDelta(-90, true, false)).toBe(-30);
  });

  it('picks allowance by the sign of the delta', () => {
    expect(resistedDelta(30, true, false)).toBe(30); // right allowed
    expect(resistedDelta(-30, false, true)).toBe(-30); // left allowed
  });
});
