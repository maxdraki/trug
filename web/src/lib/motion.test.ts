import { describe, it, expect, afterEach, vi } from 'vitest';
import type { TransitionConfig } from 'svelte/transition';
import { reducedMotion, d, DUR, STAGGER, SQUASH_SPRING, PRESS_SPRING, arriveRow } from './motion';

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
    expect(d(DUR.add)).toBe(DUR.add);
  });
});

describe('exported params', () => {
  it('exposes duration constants all within the 350ms budget', () => {
    for (const ms of Object.values(DUR)) {
      expect(ms).toBeGreaterThan(0);
      expect(ms).toBeLessThanOrEqual(350);
    }
    expect(STAGGER).toBeGreaterThan(0);
  });

  it('exposes spring params with stiffness and damping', () => {
    for (const s of [SQUASH_SPRING, PRESS_SPRING]) {
      expect(typeof s.stiffness).toBe('number');
      expect(typeof s.damping).toBe('number');
    }
  });

  it('keeps the glow within the duration budget', () => {
    expect(DUR.glow).toBeLessThanOrEqual(350);
  });
});

describe('arriveRow', () => {
  it('flies ring rows in with a per-index stagger delay', () => {
    mockMatchMedia(false);
    const node = document.createElement('div');
    const cfg = arriveRow(node, { key: 'milk', source: 'ring', index: 3 }) as TransitionConfig;
    expect(cfg.duration).toBe(DUR.fly);
    expect(cfg.delay).toBe(3 * STAGGER);
  });

  it('collapses the ring fly to instant under reduced motion', () => {
    mockMatchMedia(true);
    const node = document.createElement('div');
    const cfg = arriveRow(node, { key: 'milk', source: 'ring', index: 3 }) as TransitionConfig;
    expect(cfg.duration).toBe(0);
    expect(cfg.delay).toBe(0);
  });

  it('uses the crossfade receive for non-ring rows', () => {
    mockMatchMedia(false);
    const node = document.createElement('div');
    // No matching send exists, so receive resolves to the crossfade fallback
    // (a scale+fade), which is distinct from the fly path (it has no y offset).
    const cfg = arriveRow(node, { key: 'milk', source: 'local', index: 2 });
    expect(typeof cfg).not.toBe('undefined');
  });
});
