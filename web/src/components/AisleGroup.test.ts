import { render } from '@testing-library/svelte';
import { describe, it, expect, vi } from 'vitest';
import AisleGroup from './AisleGroup.svelte';
import type { DragController } from '../lib/drag.svelte';
import type { Item } from '../lib/types';

function item(partial: Partial<Item> & { id: string; name: string }): Item {
  return {
    note: null,
    icon: null,
    category: 'Drinks',
    status: 'active',
    source: null,
    added_by: null,
    created_at: new Date().toISOString(),
    checked_at: null,
    sort_key: 0,
    ...partial,
  };
}

/** A plain object implementing the DragController read surface for the view. */
function controller(state: Partial<DragController>): DragController {
  return {
    draggingId: null,
    dropCategory: null,
    target: null,
    originCategory: null,
    settlingId: null,
    ghostId: null,
    gapHeight: 0,
    start: vi.fn(),
    ...state,
  } as DragController;
}

const beer = item({ id: 'beer', name: 'Beer' });
const soda = item({ id: 'soda', name: 'Soda' });

function props(drag: DragController) {
  return {
    category: 'Drinks',
    items: [beer, soda],
    pendingIds: new Set<string>(),
    drag,
    onToggle: vi.fn(),
    onOpen: vi.fn(),
  };
}

describe('AisleGroup drag displacement + cleanup', () => {
  it('opens the gap on THIS aisle only (target rows shift, aisle reserves space)', () => {
    // A cross-aisle drag from Cupboard hovering Drinks at index 0: both Drinks
    // rows should descend by the gap height, and the aisle reserves +gap.
    const drag = controller({
      draggingId: 'coffee', // lifted row lives in another aisle
      originCategory: 'Cupboard',
      target: { category: 'Drinks', index: 0 },
      gapHeight: 60,
    });
    const { container } = render(AisleGroup, props(drag));

    const rows = container.querySelectorAll<HTMLElement>('[data-drag-id]');
    expect(rows.length).toBe(2);
    for (const row of rows) {
      expect(row.style.transform).toBe('translateY(60px)');
    }
    const rowsBox = container.querySelector<HTMLElement>('.rows')!;
    expect(rowsBox.style.marginBottom).toBe('60px');
  });

  it('does NOT displace rows of a bystander aisle', () => {
    // Same drag, but this rendered aisle is Household — neither origin nor target.
    const drag = controller({
      draggingId: 'coffee',
      originCategory: 'Cupboard',
      target: { category: 'Drinks', index: 0 },
      gapHeight: 60,
    });
    const { container } = render(AisleGroup, {
      ...props(drag),
      category: 'Household',
      items: [item({ id: 'wul', name: 'washing up liquid', category: 'Household' })],
    });
    const row = container.querySelector<HTMLElement>('[data-drag-id]')!;
    expect(row.style.transform).toBe('translateY(0px)');
    const rowsBox = container.querySelector<HTMLElement>('.rows')!;
    expect(rowsBox.style.marginBottom).toBe('0px');
  });

  it('clears every displaced-row transform and aisle reserve when the drag ends', async () => {
    const dragging = controller({
      draggingId: 'coffee',
      originCategory: 'Cupboard',
      target: { category: 'Drinks', index: 0 },
      gapHeight: 60,
    });
    const { container, rerender } = render(AisleGroup, props(dragging));
    // Precondition: rows are displaced mid-drag.
    expect(
      [...container.querySelectorAll<HTMLElement>('[data-drag-id]')].some(
        (r) => r.style.transform === 'translateY(60px)',
      ),
    ).toBe(true);

    // Drag ends: controller resets to idle. No orphaned inline styles may survive.
    await rerender(props(controller({})));
    for (const row of container.querySelectorAll<HTMLElement>('[data-drag-id]')) {
      expect(row.style.transform).toBe('');
      expect(row.style.opacity).toBe('');
    }
    expect(container.querySelector<HTMLElement>('.rows')!.style.marginBottom).toBe('');
  });

  it('holds the ghost-settling row invisible (opacity 0) until its clone lands', () => {
    // After a cross-aisle drop, the real re-created row is hidden while the ghost
    // flies; the drag is otherwise idle (draggingId/target cleared by cleanup).
    const drag = controller({ ghostId: 'beer', settlingId: 'beer' });
    const { container } = render(AisleGroup, props(drag));
    const beerRow = container.querySelector<HTMLElement>('[data-drag-id="beer"]')!;
    const sodaRow = container.querySelector<HTMLElement>('[data-drag-id="soda"]')!;
    expect(beerRow.style.opacity).toBe('0');
    expect(sodaRow.style.opacity).toBe('');
  });
});

describe('AisleGroup shelf label', () => {
  it('shows how many items are on the shelf', () => {
    const { container } = render(AisleGroup, props(controller({})));
    expect(container.querySelector('h2 .count')!.textContent).toBe('2');
  });

  it('counts the items it was given, not a fixed number', () => {
    // Rendered fresh rather than re-rendered: dropping a row fires the FLIP
    // animation, and jsdom has no element.getAnimations to service it.
    const { container } = render(AisleGroup, { ...props(controller({})), items: [beer] });
    expect(container.querySelector('h2 .count')!.textContent).toBe('1');
  });

  it('announces the count as part of the shelf heading', () => {
    // "Dairy & Eggs, 2" tells someone navigating by heading how much is on the
    // shelf before they enter it — the same reading the basket heading gives.
    // Both counts are announced or neither; they must not disagree.
    const { container } = render(AisleGroup, props(controller({})));
    expect(container.querySelector('h2 .count')!.getAttribute('aria-hidden')).toBeNull();
    expect(container.querySelector('h2')!.textContent!.replace(/\s+/g, ' ')).toContain('Drinks 2');
  });
});
