import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import { CATEGORY_ICON, WALK_ORDER } from './walkOrder';
import { ICONS } from './icons';

const SERVER_CATEGORIES = new URL('../../../server/trug/categories.py', import.meta.url);

/**
 * The server owns grouping semantics; this file's job is to mirror it exactly.
 * Parse the real `DEFAULT_WALK_ORDER` out of the server source so a category
 * added on one side and forgotten on the other fails here rather than in a shop.
 *
 * Two ways this used to fail for the wrong reason, both fixed here. It threw
 * ENOENT when `web/` was checked out without its sibling `server/` — a missing
 * tree is not a drifted walk order, so that case skips with a reason instead.
 * And it read only double-quoted strings off one particular line layout, so a
 * reformat of the list (single quotes, one entry per line, a trailing comment)
 * would have reported a mismatch that did not exist.
 */
function readServerSource(): string | null {
  try {
    return fs.readFileSync(SERVER_CATEGORIES, 'utf8');
  } catch {
    return null;
  }
}

function parseWalkOrder(src: string): string[] {
  // Tolerates a type annotation, either quote style, and any line layout.
  const block = /DEFAULT_WALK_ORDER\s*(?::[^=]+)?=\s*\[([\s\S]*?)\]/.exec(src);
  if (!block) throw new Error('DEFAULT_WALK_ORDER not found in server/trug/categories.py');
  // Drop `#` comments before reading the literals, so a commented-out category
  // is not counted as a live one.
  const body = block[1].replace(/#[^\n]*/g, '');
  return [...body.matchAll(/'([^']*)'|"([^"]*)"/g)].map((m) => m[1] ?? m[2]);
}

const SERVER_SRC = readServerSource();

describe('walk order', () => {
  it('mirrors the server DEFAULT_WALK_ORDER exactly, in order', (ctx) => {
    if (SERVER_SRC === null) {
      ctx.skip(
        `no server tree beside this one (${SERVER_CATEGORIES.pathname}) — nothing to compare the ` +
          'walk order against. Run this from a full checkout to check the two sides still agree.',
      );
      return;
    }
    const server = parseWalkOrder(SERVER_SRC);
    expect(server.length).toBeGreaterThan(5);
    expect(WALK_ORDER).toEqual(server);
  });

  it('shelves herbs & spices directly after the cupboard', () => {
    expect(WALK_ORDER.indexOf('Herbs & Spices')).toBe(WALK_ORDER.indexOf('Cupboard') + 1);
  });

  it('gives every aisle a shelf-label icon that exists in the generated map', () => {
    for (const category of WALK_ORDER) {
      const slug = CATEGORY_ICON[category];
      expect(slug, `no CATEGORY_ICON for ${category}`).toBeTruthy();
      expect(ICONS[slug], `icons.ts is missing ${slug}`).toBeTruthy();
    }
    expect(Object.keys(CATEGORY_ICON).sort()).toEqual([...WALK_ORDER].sort());
  });

  it('does not reuse one shelf-label icon for two aisles', () => {
    const slugs = Object.values(CATEGORY_ICON);
    expect(new Set(slugs).size).toBe(slugs.length);
  });
});
