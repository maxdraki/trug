import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import {
  contrast,
  declaration,
  FLAVOURS,
  OFFERED_ACCENTS,
  palette,
  resolveColour,
  themeVars,
} from './testing/contrast';

/**
 * The accent, used as ink.
 *
 * Aisle headers and item icons now wear the accent, and the undo notice is an
 * inverse snackbar with the accent on its action. The raw accent cannot do that
 * job everywhere: Latte's accents are saturated mid-tones, and peach or yellow
 * on a white shelf reads at around 2:1. So theme.css carries, per flavour, an
 * ink for every Catppuccin hue — the most of that hue that still clears 4.5:1 —
 * and an inverse ink for the snackbar. These tests hold the shipped CSS to that,
 * for every flavour and every accent Settings offers.
 */
const AISLE = new URL('../components/AisleGroup.svelte', import.meta.url);
const ROW = new URL('../components/ItemRow.svelte', import.meta.url);
const TOAST = new URL('../components/Toast.svelte', import.meta.url);

/**
 * Every accent theme.css can bind an ink for — read from the shipped
 * `[data-accent]` rules rather than retyped, so the list here cannot fall out
 * of step with the generator's.
 */
const THEME_CSS = fs.readFileSync(new URL('../theme.css', import.meta.url), 'utf8');
const INKED = [...THEME_CSS.matchAll(/\[data-accent="(\w+)"\]\s*\{[^}]*--accent-ink/g)].map((m) => m[1]);

describe('accent inks in theme.css', () => {
  it('measures the accents Settings actually offers', () => {
    // OFFERED_ACCENTS drives every contrast test in this suite. If Settings
    // gains an accent the list does not know about, that accent goes
    // unmeasured and everything stays green — so the two are held together.
    const src = fs.readFileSync(new URL('../components/SettingsSheet.svelte', import.meta.url), 'utf8');
    const block = /const ACCENTS = \[([\s\S]*?)\] as const;/.exec(src)?.[1] ?? '';
    const offered = [...block.matchAll(/id: '(\w+)'/g)].map((m) => m[1]);
    expect(offered.length).toBeGreaterThan(0);
    expect([...OFFERED_ACCENTS].sort()).toEqual(offered.sort());
  });

  for (const a of OFFERED_ACCENTS)
    it(`binds both of ${a}'s inks alongside the accent itself`, () => {
      // Read from the rule the browser applies, not mimicked: themeVars() binds
      // the inks by name, so a rule that lost them would pass every contrast
      // test while the app silently fell back to peach's.
      const rule = new RegExp(`\\[data-accent="${a}"\\]\\s*\\{([^}]*)\\}`).exec(THEME_CSS)?.[1] ?? '';
      expect(rule).toContain(`--accent: var(--ctp-${a});`);
      expect(rule).toContain(`--accent-ink: var(--ink-${a});`);
      expect(rule).toContain(`--accent-inverse-ink: var(--inverse-ink-${a});`);
    });

  for (const flavour of FLAVOURS)
    it(`defines both inks for every inked accent in ${flavour}`, () => {
      const p = palette(flavour);
      for (const h of INKED) {
        expect(p[`ink-${h}`], `ink-${h}`).toMatch(/^#[0-9a-f]{6}$/);
        expect(p[`inverse-ink-${h}`], `inverse-ink-${h}`).toMatch(/^#[0-9a-f]{6}$/);
      }
    });

  it('keeps the hue itself wherever the hue already reads', () => {
    // An ink is only mixed down as far as it has to be. Mocha's accents are
    // light pastels on a near-black shelf and already clear 4.5:1, so there the
    // ink must BE the accent — otherwise the dark flavours get dimmer for no
    // reason.
    const p = palette('mocha');
    for (const a of OFFERED_ACCENTS) expect(p[`ink-${a}`]).toBe(p[`ctp-${a}`]);
  });
});

describe('aisle headers in the accent', () => {
  const fg = declaration(AISLE, 'h2', 'color')!;
  const bg = declaration(AISLE, 'h2', 'background')!;
  const icon = declaration(AISLE, '.cat-ico', 'color')!;

  it('wear the accent ink', () => {
    expect(fg).toBe('var(--accent-ink)');
  });

  for (const flavour of FLAVOURS)
    for (const accent of OFFERED_ACCENTS) {
      it(`read at 4.5:1 in ${flavour} on ${accent}`, () => {
        const v = themeVars(flavour, accent);
        expect(contrast(resolveColour(fg, v), resolveColour(bg, v))).toBeGreaterThanOrEqual(4.5);
      });
      it(`show their icon at 3:1 in ${flavour} on ${accent}`, () => {
        const v = themeVars(flavour, accent);
        expect(contrast(resolveColour(icon, v), resolveColour(bg, v))).toBeGreaterThanOrEqual(3);
      });
    }
});

describe('item icons without their square', () => {
  const lineFg = declaration(ROW, '.chip.line', 'color')!;
  const monoFg = declaration(ROW, '.chip.monogram', 'color')!;

  it('draw no square behind an icon or a letter', () => {
    // The icon sits straight on the shelf now, as Material's list items do. The
    // square went; the space it held did not, so rows keep their height.
    expect(declaration(ROW, '.chip', 'background')).toBeNull();
    expect(declaration(ROW, '.chip.monogram', 'background')).toBeNull();
  });

  for (const flavour of FLAVOURS)
    for (const accent of OFFERED_ACCENTS) {
      it(`show the icon at 3:1 on the shelf in ${flavour} on ${accent}`, () => {
        // A line icon is a graphic, so 3:1 is its floor.
        const v = themeVars(flavour, accent);
        expect(contrast(resolveColour(lineFg, v), resolveColour('var(--ctp-base)', v))).toBeGreaterThanOrEqual(3);
      });
      it(`read a letter at 4.5:1 on the shelf in ${flavour} on ${accent}`, () => {
        // A letter is text, so it gets the text floor.
        const v = themeVars(flavour, accent);
        expect(contrast(resolveColour(monoFg, v), resolveColour('var(--ctp-base)', v))).toBeGreaterThanOrEqual(4.5);
      });
    }
});

describe('undo snackbar', () => {
  const bg = declaration(TOAST, '.toast.undo', 'background')!;
  const fg = declaration(TOAST, '.toast.undo', 'color')!;
  const action = declaration(TOAST, '.toast.undo :global(button)', 'color')!;
  const ring = declaration(TOAST, '.toast.undo :global(button:focus-visible)', 'outline-color')!;

  for (const flavour of FLAVOURS)
    for (const accent of OFFERED_ACCENTS) {
      it(`reads its message at 4.5:1 in ${flavour} on ${accent}`, () => {
        const v = themeVars(flavour, accent);
        expect(contrast(resolveColour(fg, v), resolveColour(bg, v))).toBeGreaterThanOrEqual(4.5);
      });
      it(`shows its keyboard focus ring at 3:1 in ${flavour} on ${accent}`, () => {
        // The accent ring the rest of the app uses measures 1.0–2.7:1 here.
        const v = themeVars(flavour, accent);
        expect(contrast(resolveColour(ring, v), resolveColour(bg, v))).toBeGreaterThanOrEqual(3);
      });
      it(`reads Undo at 4.5:1 in ${flavour} on ${accent}`, () => {
        // "Undo" is a word, not an icon: text floor.
        const v = themeVars(flavour, accent);
        expect(contrast(resolveColour(action, v), resolveColour(bg, v))).toBeGreaterThanOrEqual(4.5);
      });
    }
});

describe('frequently-added pills', () => {
  // The pills carry a stronger accent wash than the aisle bands: they are the
  // "things you'll likely need" and should read as offers, not as more list.
  // The label stays --ctp-text, so a stronger wash is only safe while the label
  // still reads on it — at rest and on hover.
  const PILLS = new URL('../components/RecentsGrid.svelte', import.meta.url);
  const fg = declaration(PILLS, '.pill', 'color')!;
  const rest = declaration(PILLS, '.pill', 'background')!;
  const hover = declaration(PILLS, '.pill:hover', 'background')!;

  it('are tinted more strongly than the aisle bands', () => {
    const pct = (e: string) => Number(/var\(--accent\) (\d+)%/.exec(e)?.[1]);
    const band = declaration(new URL('../app.css', import.meta.url), ':root', '--band-fill')!;
    expect(pct(rest)).toBeGreaterThan(pct(band));
  });

  for (const flavour of FLAVOURS)
    for (const accent of OFFERED_ACCENTS) {
      it(`read their label at 4.5:1 at rest in ${flavour} on ${accent}`, () => {
        const v = themeVars(flavour, accent);
        expect(contrast(resolveColour(fg, v), resolveColour(rest, v))).toBeGreaterThanOrEqual(4.5);
      });
      it(`read their label at 4.5:1 on hover in ${flavour} on ${accent}`, () => {
        const v = themeVars(flavour, accent);
        expect(contrast(resolveColour(fg, v), resolveColour(hover, v))).toBeGreaterThanOrEqual(4.5);
      });
    }
});

describe('aisle count on the band', () => {
  const count = declaration(AISLE, 'h2 .count', 'color')!;
  const bg = declaration(AISLE, 'h2', 'background')!;
  for (const flavour of FLAVOURS)
    for (const accent of OFFERED_ACCENTS)
      it(`reads at 4.5:1 in ${flavour} on ${accent}`, () => {
        const v = themeVars(flavour, accent);
        expect(contrast(resolveColour(count, v), resolveColour(bg, v))).toBeGreaterThanOrEqual(4.5);
      });
});

describe('swipe-to-basket tick', () => {
  // The field is a wash over the row's ground, which is the shelf.
  const tick = declaration(ROW, '.swipe-field.basket', 'color')!;
  for (const flavour of FLAVOURS)
    for (const accent of OFFERED_ACCENTS)
      it(`shows at 3:1 on its field in ${flavour} on ${accent}`, () => {
        const v = themeVars(flavour, accent);
        const field = resolveColour('color-mix(in srgb, var(--accent) 14%, var(--ctp-base))', v);
        expect(contrast(resolveColour(tick, v), field)).toBeGreaterThanOrEqual(3);
      });
});
