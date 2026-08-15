import { describe, it, expect, afterEach } from 'vitest';
import { applyTheme, applyDensity, loadTheme } from './theme';
import fs from 'node:fs';

it('theme.css defines all four flavours with no missing vars', () => {
  const css = fs.readFileSync(new URL('../theme.css', import.meta.url), 'utf8');
  for (const f of ['mocha', 'latte', 'frappe', 'macchiato'])
    expect(css).toContain(`[data-flavour="${f}"]`);
  expect(css.match(/--ctp-base:/g)!.length).toBeGreaterThanOrEqual(4);
});

it('applyTheme sets and persists the flavour', () => {
  applyTheme('latte', 'mauve');
  expect(document.documentElement.dataset.flavour).toBe('latte');
  expect(localStorage.getItem('trug_flavour')).toBe('latte');
});

it('applyDensity sets and persists the density', () => {
  applyDensity('dense');
  expect(document.documentElement.dataset.density).toBe('dense');
  expect(localStorage.getItem('trug_density')).toBe('dense');
});

it('applyDensity(null) clears the density attribute and storage', () => {
  applyDensity('dense');
  applyDensity(null);
  expect(document.documentElement.dataset.density).toBeUndefined();
  expect(localStorage.getItem('trug_density')).toBeNull();
});

it('loadTheme restores the persisted density', () => {
  applyDensity('dense');
  delete document.documentElement.dataset.density;
  loadTheme();
  expect(document.documentElement.dataset.density).toBe('dense');
});

/**
 * A browser with site data blocked throws on the `localStorage` LOOKUP, not on
 * the call. `loadTheme()` runs at main.ts:12 before the app mounts, so an
 * unguarded read there was a blank page rather than a lost preference.
 */
describe('blocked storage', () => {
  const real = Object.getOwnPropertyDescriptor(window, 'localStorage')!;

  function block(): void {
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() {
        throw new DOMException('The operation is insecure.', 'SecurityError');
      },
    });
  }

  afterEach(() => {
    Object.defineProperty(window, 'localStorage', real);
    localStorage.clear();
    delete document.documentElement.dataset.flavour;
    delete document.documentElement.dataset.accent;
    delete document.documentElement.dataset.density;
  });

  it('boots with the default theme instead of throwing', () => {
    block();
    expect(() => loadTheme()).not.toThrow();
  });

  it('still applies a chosen theme to the page it cannot persist it for', () => {
    block();
    expect(() => applyTheme('latte', 'mauve')).not.toThrow();
    expect(document.documentElement.dataset.flavour).toBe('latte');
  });

  it('still applies a chosen density', () => {
    block();
    expect(() => applyDensity('dense')).not.toThrow();
    expect(document.documentElement.dataset.density).toBe('dense');
  });
});
