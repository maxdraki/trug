import { render, screen, fireEvent } from '@testing-library/svelte';
import { describe, it, expect, vi } from 'vitest';
import ItemRow from './ItemRow.svelte';
import type { Item } from '../lib/types';
import { DUR } from '../lib/motion';
import { AXIS_LOCK_PX } from '../lib/swipe';
import { pointerEvent } from '../lib/testing/pointer';
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
    render(ItemRow, { item: item({ id: '1', name: 'Kryptonite', icon: null }), onToggle: vi.fn() });
    expect(screen.getByText('K')).toBeTruthy();
  });

  it('resolves a line icon from the name when icon is null but the name maps to a slug', () => {
    // Catalogue/legacy rows can store icon:null; the name-derived slug ('milk')
    // is in the icon map, so a real line icon renders instead of a bare letter.
    const { container } = render(ItemRow, {
      item: item({ id: '2', name: 'Milk', icon: null }),
      onToggle: vi.fn(),
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
    render(ItemRow, { item: item({ id: 'abc', name: 'Milk' }), onToggle });
    await fireEvent.click(screen.getByRole('button', { name: /^Milk$/ }));
    expect(onToggle).toHaveBeenCalledWith('abc');
  });
});

/** Drag the row to `toX` and let go — far enough to commit by distance. */
function swipeTo(container: HTMLElement, toX: number) {
  const row = container.querySelector('.row') as HTMLElement;
  const content = container.querySelector('.swipe-content') as HTMLElement;
  // Distance commit is a fraction of the row's width, and jsdom lays nothing out.
  Object.defineProperty(row, 'offsetWidth', { value: 300, configurable: true });
  const lead = toX > 0 ? AXIS_LOCK_PX + 5 : -(AXIS_LOCK_PX + 5);
  row.dispatchEvent(pointerEvent('pointerdown', { x: 0 }));
  window.dispatchEvent(pointerEvent('pointermove', { x: lead }));
  window.dispatchEvent(pointerEvent('pointermove', { x: toX }));
  window.dispatchEvent(pointerEvent('pointerup', { x: toX }));
  return { row, content };
}

// A committed swipe must finish the journey the thumb started, in the direction
// it was thrown. Left already did; right snapped the row home in a single frame
// from up to ~190px out and then played the tap check-off instead. That is not a
// slower or plainer animation, it is a discontinuity — and the grammar was
// inverted with it: a REJECTED swipe rubber-bands home over 200ms, so failure
// had smoother feedback than success and the two were indistinguishable for the
// first frame. Direction, colour and glyph carry what the gesture MEANS; the
// kinematics are the same on both sides.
describe('ItemRow swipe commit', () => {
  it('carries a committed right-swipe off to the right, like a delete leaves left', () => {
    const { container } = render(ItemRow, {
      item: item({ id: 'pk', name: 'Pine Kernels' }),
      onToggle: vi.fn(),
      onSwipeRight: vi.fn(),
      onSwipeLeft: vi.fn(),
    });

    const { content } = swipeTo(container as HTMLElement, 150);

    expect(content.style.transform).toBe('translateX(100%)');
    expect(content.style.transition).toContain(`${DUR.swipeCommit}ms`);
  });

  it('leaves the basket field showing under the departing row', () => {
    // The delete path does not clear its trash field either: the field belongs
    // to the row that is leaving and should go when its box does, not a frame
    // before it starts moving.
    const { container } = render(ItemRow, {
      item: item({ id: 'pk', name: 'Pine Kernels' }),
      onToggle: vi.fn(),
      onSwipeRight: vi.fn(),
      onSwipeLeft: vi.fn(),
    });

    swipeTo(container as HTMLElement, 150);

    const field = container.querySelector('.swipe-field.basket') as HTMLElement;
    expect(field.style.opacity).not.toBe('0');
  });

  it('still carries a committed left-swipe off to the left', () => {
    // The path this change is matching — guards against fixing one by breaking
    // the other.
    const { container } = render(ItemRow, {
      item: item({ id: 'pk', name: 'Pine Kernels' }),
      onToggle: vi.fn(),
      onSwipeRight: vi.fn(),
      onSwipeLeft: vi.fn(),
    });

    const { content } = swipeTo(container as HTMLElement, -150);

    expect(content.style.transform).toBe('translateX(-100%)');
    expect(content.style.transition).toContain(`${DUR.swipeCommit}ms`);
  });

  it('commits the check-off on the spot, without waiting for the slide', () => {
    // The hold system's proudest property (lib/hold.svelte.ts): presentation
    // never delays a mutation, so a hold that is dropped costs an animation and
    // never a check-off. The left path can afford its 60% handoff because a
    // delete has an undo behind it; this one must not start queueing.
    const onSwipeRight = vi.fn();
    const { container } = render(ItemRow, {
      item: item({ id: 'pk', name: 'Pine Kernels' }),
      onToggle: vi.fn(),
      onSwipeRight,
      onSwipeLeft: vi.fn(),
    });

    swipeTo(container as HTMLElement, 150);

    expect(onSwipeRight).toHaveBeenCalledTimes(1);
  });
});
