import { render, screen, fireEvent } from '@testing-library/svelte';
import { describe, it, expect, vi } from 'vitest';
import ItemRow from './ItemRow.svelte';
import type { Item } from '../lib/types';
import {
  contrast,
  declaration,
  FLAVOURS,
  OFFERED_ACCENTS,
  resolveColour,
  themeVars,
} from '../lib/testing/contrast';

function item(partial: Partial<Item> & { id: string; name: string }): Item {
  return {
    note: null,
    icon: null,
    category: 'Other',
    status: 'active',
    source: null,
    added_by: null,
    created_at: new Date().toISOString(),
    checked_at: null,
    sort_key: 0,
    ...partial,
  };
}

describe('ItemRow', () => {
  it('renders a monogram (first letter) when icon is null and the name has no slug', () => {
    // 'Kryptonite' has no matching icon slug, so the chip falls back to a monogram.
    render(ItemRow, { item: item({ id: '1', name: 'Kryptonite', icon: null }), onToggle: vi.fn(), onRemove: vi.fn() });
    expect(screen.getByText('K')).toBeTruthy();
  });

  it('resolves a line icon from the name when icon is null but the name maps to a slug', () => {
    // Catalogue/legacy rows can store icon:null; the name-derived slug ('milk')
    // is in the icon map, so a real line icon renders instead of a bare letter.
    const { container } = render(ItemRow, {
      item: item({ id: '2', name: 'Milk', icon: null }),
      onToggle: vi.fn(),
      onRemove: vi.fn(),
    });
    expect(screen.queryByText('M')).toBeNull();
    expect(container.querySelector('svg')).toBeTruthy();
  });

  // The monogram is the one chip whose glyph is TEXT, so it answers to WCAG's
  // 4.5:1 and not to the 3:1 a line icon gets as a graphic. That difference is
  // the whole reason it cannot simply copy `.chip.line`'s "glyph = --accent":
  // Latte's accents over a wash of themselves land between 2.0:1 (yellow, pink)
  // and 3.8:1 (mauve). Asserted against the shipped declarations rather than a
  // transcription of them, so retuning the treatment re-measures it.
  describe('monogram chip contrast', () => {
    const file = new URL('./ItemRow.svelte', '' + import.meta.url);
    const fg = declaration(file, '.chip.monogram', 'color') ?? declaration(file, '.chip', 'color')!;
    const bg =
      declaration(file, '.chip.monogram', 'background') ?? declaration(file, '.chip', 'background')!;

    for (const flavour of FLAVOURS)
      for (const accent of OFFERED_ACCENTS)
        it(`clears 4.5:1 in ${flavour} on ${accent}`, () => {
          const vars = themeVars(flavour, accent);
          const ratio = contrast(resolveColour(fg, vars), resolveColour(bg, vars));
          expect(ratio).toBeGreaterThanOrEqual(4.5);
        });
  });

  it('calls onToggle with the item id when the row is clicked', async () => {
    const onToggle = vi.fn();
    render(ItemRow, { item: item({ id: 'abc', name: 'Milk' }), onToggle, onRemove: vi.fn() });
    await fireEvent.click(screen.getByRole('button', { name: /^Milk$/ }));
    expect(onToggle).toHaveBeenCalledWith('abc');
  });
});
