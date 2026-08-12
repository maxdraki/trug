import { describe, it, expect } from 'vitest';
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
