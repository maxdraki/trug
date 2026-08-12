import fs from 'node:fs';

/**
 * Test-only colour maths, for asserting a treatment's contrast rather than
 * reasoning about it.
 *
 * The app has shipped one accent-on-tinted-background regression already —
 * accent-tinted pill text measured 3.39:1 in Latte on peach — and the reason it
 * got through is that "an accent on a 16% wash of itself" sounds safe and is
 * not. Latte's accents are dark-on-light hues chosen to be READ against base;
 * mixed into base at 16% they make a background that is still nearly base, so a
 * treatment that clears 8:1 in Mocha can sit at 2:1 in Latte. The only way to
 * know is to compute it, per flavour and per accent, which is what this is for.
 *
 * The order of operations is the part that is easy to get wrong: CSS
 * `color-mix(in srgb, …)` interpolates the GAMMA-ENCODED channels, and WCAG
 * linearises afterwards. Mix first, linearise second — doing it the other way
 * round flatters every mix by a few tenths.
 */

/** `#rrggbb` → three 0–255 channels. */
function channels(hex: string): [number, number, number] {
  const h = hex.trim();
  return [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
}

function toHex(rgb: number[]): string {
  return '#' + rgb.map((c) => Math.round(c).toString(16).padStart(2, '0')).join('');
}

/** Split on commas that are not inside parentheses. */
function topLevelSplit(s: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < s.length; i++) {
    if (s[i] === '(') depth++;
    else if (s[i] === ')') depth--;
    else if (s[i] === ',' && depth === 0) {
      parts.push(s.slice(start, i));
      start = i + 1;
    }
  }
  parts.push(s.slice(start));
  return parts.map((p) => p.trim());
}

/**
 * Resolve a CSS colour expression to `#rrggbb`, given a variable map (keys
 * without the leading `--`). Understands the three forms this stylesheet uses:
 * a literal hex, `var(--name)` (resolved recursively), and
 * `color-mix(in srgb, <colour> <pct>%, <colour>)`.
 */
export function resolveColour(expr: string, vars: Record<string, string>): string {
  const e = expr.trim();
  if (e.startsWith('#')) return e.length === 7 ? e : toHex(channels(e));

  const varMatch = /^var\(\s*--([\w-]+)\s*(?:,([^]*))?\)$/.exec(e);
  if (varMatch) {
    const value = vars[varMatch[1]];
    if (value === undefined) {
      if (varMatch[2] !== undefined) return resolveColour(varMatch[2], vars);
      throw new Error(`unknown custom property --${varMatch[1]}`);
    }
    return resolveColour(value, vars);
  }

  if (e.startsWith('color-mix(')) {
    const args = topLevelSplit(e.slice('color-mix('.length, e.lastIndexOf(')')));
    const space = args[0].trim();
    if (space !== 'in srgb') throw new Error(`unsupported color-mix space: ${space}`);
    const operands = args.slice(1).map((a) => {
      const pct = /\s([\d.]+)%$/.exec(a);
      return {
        colour: pct ? a.slice(0, pct.index).trim() : a,
        weight: pct ? Number(pct[1]) / 100 : null,
      };
    });
    if (operands.length !== 2) throw new Error(`color-mix needs two colours: ${e}`);
    const [a, b] = operands;
    const wa = a.weight ?? (b.weight !== null ? 1 - b.weight : 0.5);
    const wb = b.weight ?? 1 - wa;
    const ca = channels(resolveColour(a.colour, vars));
    const cb = channels(resolveColour(b.colour, vars));
    // Gamma-encoded per-channel mix, exactly as sRGB color-mix does it.
    return toHex(ca.map((c, i) => (c * wa + cb[i] * wb) / (wa + wb)));
  }

  throw new Error(`cannot resolve colour: ${e}`);
}

/** WCAG relative luminance of a `#rrggbb`. */
export function luminance(hex: string): number {
  const [r, g, b] = channels(hex).map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio between two resolved colours, 1–21. */
export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

export const FLAVOURS = ['latte', 'frappe', 'macchiato', 'mocha'] as const;

/**
 * The accents Settings actually offers (SettingsSheet's `ACCENTS`). theme.css
 * maps every Catppuccin name to `--accent`, but only these six are reachable by
 * a shopper, so only these six are what a contrast floor has to hold across.
 */
export const OFFERED_ACCENTS = ['peach', 'mauve', 'green', 'blue', 'pink', 'yellow'] as const;

/** Every `--ctp-*` of one flavour, read from the generated theme.css. */
export function palette(flavour: string): Record<string, string> {
  // The concat keeps `import.meta.url` out of the `new URL(<literal>, …)` shape
  // Vite's asset plugin rewrites into a served-asset URL (see vite.config.ts —
  // its `pre` transform does the same thing, but only for *.test.ts files).
  const css = fs.readFileSync(new URL('../../theme.css', '' + import.meta.url), 'utf8');
  const block = new RegExp(`\\[data-flavour="${flavour}"\\]\\s*\\{([^}]*)\\}`).exec(css);
  if (!block) throw new Error(`theme.css has no flavour ${flavour}`);
  const vars: Record<string, string> = {};
  for (const m of block[1].matchAll(/--([\w-]+):\s*([^;]+);/g)) vars[m[1]] = m[2].trim();
  return vars;
}

/** A flavour's palette with `--accent` bound to one of its hues. */
export function themeVars(flavour: string, accent: string): Record<string, string> {
  const vars = palette(flavour);
  return { ...vars, accent: vars[`ctp-${accent}`] };
}

/**
 * Pull one declaration out of one rule of a `.svelte` file's `<style>` block,
 * so a test can assert against the CSS that actually ships rather than a copy
 * of it. `selector` is matched literally and must be followed by `{`.
 */
export function declaration(file: URL, selector: string, property: string): string | null {
  const css = fs.readFileSync(file, 'utf8');
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const rule = new RegExp(`(?:^|[\\s}])${escaped}\\s*\\{([^}]*)\\}`, 'm').exec(css);
  if (!rule) throw new Error(`no rule for selector ${selector}`);
  const decl = new RegExp(`(?:^|;)\\s*${property}\\s*:\\s*([^;]+)`).exec(rule[1]);
  return decl ? decl[1].trim() : null;
}
