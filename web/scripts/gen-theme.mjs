// Generates src/theme.css from the Catppuccin palette.
// Run: node scripts/gen-theme.mjs > src/theme.css
// Emits, per flavour, a [data-flavour="..."] block covering all 26 colors,
// plus a :root default of Mocha and a light-scheme default of Latte (only
// when no explicit data-flavour is set). Accent is indirected through
// --accent, overridable via data-accent.
import { flavors } from '@catppuccin/palette';

const FLAVOURS = ['mocha', 'latte', 'frappe', 'macchiato'];

// Latte's three ground surfaces, lifted toward white. Stock Latte grounds the
// app in a pale grey-blue (base #eff1f5, mantle #e6e9ef, crust #dce0e8), and on a
// phone in daylight the whole list read as pastel. Only these three move: the
// hues, the text ramp and the surface/overlay steps are Catppuccin's, so every
// accent, icon and rule stays exactly the colour it was. The ladder keeps its
// order — page (crust) under column (mantle) under shelf (base) — just nearer
// white. Done here rather than overridden in CSS so theme.css stays the one
// source the app and its contrast tests both read.
const LATTE_LIFT = { base: 0.85, mantle: 0.55, crust: 0.35 };

function towardWhite(hex, t) {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return '#' + c.map((v) => Math.round(v + (255 - v) * t).toString(16).padStart(2, '0')).join('');
}

/** The flavour's colours as shipped: Catppuccin's, with Latte's grounds lifted. */
function colours(flavour) {
  return Object.fromEntries(
    Object.entries(flavors[flavour].colors).map(([name, c]) => {
      const lift = flavour === 'latte' ? LATTE_LIFT[name] : undefined;
      return [name, lift ? towardWhite(c.hex, lift) : c.hex];
    }),
  );
}

// --- accent inks -------------------------------------------------------------
// The accent now does text-sized jobs — aisle headers, item icons, the undo
// action — and the raw hue cannot do them everywhere: Latte's accents are
// saturated mid-tones, so peach or yellow on a white shelf reads at about 2:1.
// For every hue a shopper can pick as their accent, emit:
//   --ink-<hue>          the most of that hue that still reads at 4.5:1 on the
//                        shelf (base) and the accent-tinted aisle band, mixed toward
//                        --ctp-text only as far as it has to be. Where the hue
//                        already reads — every accent in Mocha — it IS the hue.
//   --inverse-ink-<hue>  the same, for the inverse snackbar, whose ground is
//                        --ctp-text. It starts from the OTHER theme's version
//                        of the hue — Latte's for the dark flavours, Mocha's for
//                        Latte — which is what Material means by "inverse
//                        primary": the inverted surface borrows the opposite
//                        theme's colour. Darkening Mocha's own pastel peach
//                        toward its navy base instead came out a muddy
//                        #6d544e; starting from Latte's saturated peach keeps
//                        it a real orange.
// The floor is 4.6 rather than 4.5 so the faint accent tint on the dark aisle
// bands cannot tip a borderline ink under; src/lib/accentInk.test.ts measures
// the exact grounds the app paints.
const INK_HUES = ['rosewater', 'flamingo', 'pink', 'mauve', 'red', 'maroon', 'peach', 'yellow',
  'green', 'teal', 'sky', 'sapphire', 'blue', 'lavender'];
const INK_FLOOR = 4.6;
// The aisle band is the accent washed through mantle at this strength (app.css
// --band-fill), and the header ink has to read on it as well as on the plain
// shelf. The two numbers are one decision: lib/accentInk.test.ts measures the
// header against the band app.css actually paints, so if they drift apart that
// test is where it shows.
const BAND_TINT = 0.1;

// WCAG relative luminance and sRGB color-mix, the same maths as the contrast
// helpers in src/lib/testing/contrast.ts. It is a second copy on purpose: this
// is a dependency-free Node script and that is a TypeScript test module, and
// sharing one would mean a loader in a build step that deliberately has none.
// The tests measure what this emits with the other copy, so a divergence
// between the two shows up as a failing contrast test, not as a silent drift.
const channel = (hex, i) => parseInt(hex.slice(1 + 2 * i, 3 + 2 * i), 16);
const linear = (v) => ((v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
const luminance = (hex) => [0.2126, 0.7152, 0.0722].reduce((sum, w, i) => sum + w * linear(channel(hex, i)), 0);
function ratio(a, b) {
  const [x, y] = [luminance(a), luminance(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}
// The same gamma-encoded mix `color-mix(in srgb, a t, b)` performs.
const mix = (a, b, t) =>
  '#' + [0, 1, 2].map((i) => Math.round(channel(a, i) * t + channel(b, i) * (1 - t)).toString(16).padStart(2, '0')).join('');

function ink(hue, toward, grounds) {
  for (let share = 100; share >= 0; share -= 2) {
    const c = mix(hue, toward, share / 100);
    if (grounds.every((g) => ratio(c, g) >= INK_FLOOR)) return c;
  }
  return toward;
}

function vars(flavour, indent) {
  const pad = ' '.repeat(indent);
  const c = colours(flavour);
  const lines = Object.entries(c).map(([name, hex]) => `${pad}--ctp-${name}: ${hex};`);
  const opposite = colours(flavour === 'latte' ? 'mocha' : 'latte');
  for (const h of INK_HUES) {
    const band = mix(c[h], c.mantle, BAND_TINT);
    lines.push(`${pad}--ink-${h}: ${ink(c[h], c.text, [c.base, c.mantle, band])};`);
    lines.push(`${pad}--inverse-ink-${h}: ${ink(opposite[h], c.base, [c.text])};`);
  }
  return lines.join('\n');
}

function accentBlock(name, indent) {
  const pad = ' '.repeat(indent);
  return [
    `${pad}--accent: var(--ctp-${name});`,
    `${pad}--accent-ink: var(--ink-${name});`,
    `${pad}--accent-inverse-ink: var(--inverse-ink-${name});`,
  ].join('\n');
}

let out = `/* GENERATED by scripts/gen-theme.mjs — do not edit by hand. */\n`;

// Explicit flavour selection.
for (const f of FLAVOURS) {
  out += `\n[data-flavour="${f}"] {\n${vars(f, 2)}\n}\n`;
}

// Defaults when no explicit data-flavour attribute is set: Mocha (dark),
// overridden by Latte under a light colour-scheme preference.
out += `\n:root:not([data-flavour]) {\n${vars('mocha', 2)}\n}\n`;
out += `\n@media (prefers-color-scheme: light) {\n  :root:not([data-flavour]) {\n${vars('latte', 4)}\n  }\n}\n`;

// Accent indirection: default to peach, overridable via data-accent="<color>".
// The inks travel with it, so whatever sets the accent sets its inks too.
out += `\n:root {\n${accentBlock('peach', 2)}\n}\n`;
for (const [name] of Object.entries(flavors.mocha.colors)) {
  out += INK_HUES.includes(name)
    ? `[data-accent="${name}"] {\n${accentBlock(name, 2)}\n}\n`
    : `[data-accent="${name}"] { --accent: var(--ctp-${name}); }\n`;
}

process.stdout.write(out);
