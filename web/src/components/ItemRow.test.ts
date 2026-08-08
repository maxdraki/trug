import { render, screen, fireEvent } from '@testing-library/svelte';
import { describe, it, expect, vi } from 'vitest';
import ItemRow from './ItemRow.svelte';
import type { Item } from '../lib/types';

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

  it('calls onToggle with the item id when the row is clicked', async () => {
    const onToggle = vi.fn();
    render(ItemRow, { item: item({ id: 'abc', name: 'Milk' }), onToggle, onRemove: vi.fn() });
    await fireEvent.click(screen.getByRole('button', { name: /^Milk$/ }));
    expect(onToggle).toHaveBeenCalledWith('abc');
  });
});
