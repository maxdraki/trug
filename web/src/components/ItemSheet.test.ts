import { render, screen, fireEvent } from '@testing-library/svelte';
import { describe, it, expect, vi } from 'vitest';
import ItemSheet from './ItemSheet.svelte';
import type { Item } from '../lib/types';

const WALK_ORDER = [
  'Fruit & Veg', 'Bakery', 'Meat & Fish', 'Dairy & Eggs', 'Cupboard',
  'Frozen', 'Drinks', 'Household', 'Pet', 'Other',
];

function item(partial: Partial<Item> & { id: string; name: string }): Item {
  return {
    note: null,
    icon: null,
    category: null,
    status: 'active',
    source: null,
    added_by: null,
    created_at: new Date().toISOString(),
    checked_at: null,
    sort_key: 0,
    ...partial,
  };
}

describe('ItemSheet', () => {
  it('saves the exact selected category string (round-trip pin)', async () => {
    const onSave = vi.fn(() => Promise.resolve());
    render(ItemSheet, {
      item: item({ id: 'x', name: 'Cat food', category: 'Other' }),
      walkOrder: WALK_ORDER,
      onSave,
      onRemove: vi.fn(),
      onClose: vi.fn(),
    });

    const select = screen.getByLabelText('Category') as HTMLSelectElement;
    await fireEvent.change(select, { target: { value: 'Pet' } });

    await fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(onSave).toHaveBeenCalledWith('x', { note: null, category: 'Pet' });
  });
});
