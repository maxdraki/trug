import { describe, it, expect } from 'vitest';
import { applyTheme } from './theme';
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
