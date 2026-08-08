import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import { describe, it, expect, vi } from 'vitest';
import AddBar from './AddBar.svelte';
import type { CatalogEntry } from '../lib/types';

function entry(display_name: string): CatalogEntry {
  return { name_norm: display_name.toLowerCase(), display_name, icon: null, category: null, times_added: 1 };
}

describe('AddBar', () => {
  it('adds the top suggestion on Enter', async () => {
    const onAdd = vi.fn();
    const search = vi.fn(async (_q: string): Promise<CatalogEntry[]> => [entry('Milk'), entry('Millet')]);
    render(AddBar, { search, onAdd });

    const input = screen.getByRole('textbox');
    await fireEvent.input(input, { target: { value: 'mi' } });

    // Wait for the debounced typeahead to populate.
    await waitFor(() => expect(search).toHaveBeenCalled());
    await screen.findByText('Milk');

    await fireEvent.keyDown(input, { key: 'Enter' });
    expect(onAdd).toHaveBeenCalledWith('Milk');
  });

  it('adds the raw text on Enter when there are no suggestions', async () => {
    const onAdd = vi.fn();
    const search = vi.fn(async (): Promise<CatalogEntry[]> => []);
    render(AddBar, { search, onAdd });

    const input = screen.getByRole('textbox');
    await fireEvent.input(input, { target: { value: 'Dragonfruit' } });
    await waitFor(() => expect(search).toHaveBeenCalled());

    await fireEvent.keyDown(input, { key: 'Enter' });
    expect(onAdd).toHaveBeenCalledWith('Dragonfruit');
  });

  it('clears the pending debounce timer on commit so no stray search fires after an add', async () => {
    const onAdd = vi.fn();
    const search = vi.fn(async (): Promise<CatalogEntry[]> => []);
    render(AddBar, { search, onAdd });

    const input = screen.getByRole('textbox');
    await fireEvent.input(input, { target: { value: 'Egg' } });
    // Commit immediately, before the 120ms debounce timer fires.
    await fireEvent.keyDown(input, { key: 'Enter' });
    expect(onAdd).toHaveBeenCalledWith('Egg');

    // Wait past the debounce window; the pending search must not fire.
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(search).not.toHaveBeenCalled();
  });
});
