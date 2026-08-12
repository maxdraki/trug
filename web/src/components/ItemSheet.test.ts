import { render, screen, fireEvent } from '@testing-library/svelte';
import { describe, it, expect, vi } from 'vitest';
import ItemSheet from './ItemSheet.svelte';
import type { Item } from '../lib/types';

// The real list the app passes in, so the picker can never quietly offer a
// stale set of aisles.
import { WALK_ORDER } from '../lib/walkOrder';

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

  it('offers every aisle in the walk order, including Herbs & Spices', async () => {
    render(ItemSheet, {
      item: item({ id: 'x', name: 'Oregano', category: 'Other' }),
      walkOrder: WALK_ORDER,
      onSave: vi.fn(() => Promise.resolve()),
      onRemove: vi.fn(),
      onClose: vi.fn(),
    });

    const select = screen.getByLabelText('Category') as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(WALK_ORDER);
    expect(WALK_ORDER).toContain('Herbs & Spices');
  });
});
