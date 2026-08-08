import { ICONS } from './icons';

/**
 * How a chip should render an item/catalogue entry:
 *  - `line`     — a Tabler line icon keyed by `slug` (from icons.ts);
 *  - `glyph`    — a legacy emoji rendered verbatim as text;
 *  - `monogram` — the first letter, when nothing else resolves.
 */
export type IconResolution =
  | { kind: 'line'; slug: string }
  | { kind: 'glyph'; glyph: string }
  | { kind: 'monogram' };

/**
 * Normalise a display name to a candidate icon slug: lowercase, non-alphanumeric
 * runs collapsed to single hyphens. Mirrors the server's `name_norm` closely
 * enough that single-word grocery names map onto their Tabler slug.
 */
export function slugForName(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

/**
 * The single chip-resolution path shared by list rows and the recents tray.
 *
 * An ASCII `icon` is a slug into the generated line-icon map; a non-ASCII value
 * is a legacy emoji, rendered as a glyph. When there is no usable `icon` slug
 * (older catalogue rows predate the icon map and store `null`), fall back to the
 * item's own name: if its normalised slug is in the map, render that line icon.
 */
export function resolveIcon(icon: string | null | undefined, name: string): IconResolution {
  if (icon) {
    if (/^[\x20-\x7e]+$/.test(icon)) {
      if (icon in ICONS) return { kind: 'line', slug: icon };
    } else {
      return { kind: 'glyph', glyph: icon };
    }
  }
  const slug = slugForName(name);
  if (slug in ICONS) return { kind: 'line', slug };
  return { kind: 'monogram' };
}
