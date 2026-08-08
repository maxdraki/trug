import { describe, it, expect, beforeEach } from 'vitest';
import {
  readSnapshot,
  writeSnapshot,
  clearSnapshot,
  hasSnapshot,
  formatAge,
  staleHint,
  SNAPSHOT_STALE_MS,
} from './snapshot';
import type { Item } from './types';

function item(partial: Partial<Item> & { id: string; name: string }): Item {
  return {
    note: null,
    icon: null,
    category: 'Other',
    status: 'active',
    source: null,
    added_by: null,
    created_at: '2026-08-05T00:00:00Z',
    checked_at: null,
    sort_key: 0,
    ...partial,
  };
}

describe('snapshot store', () => {
  beforeEach(() => clearSnapshot());

  it('round-trips items and savedAt', () => {
    const items = [item({ id: 'a', name: 'Milk', category: 'Dairy' })];
    writeSnapshot(items, 1_700_000_000_000);

    const snap = readSnapshot();
    expect(snap).not.toBeNull();
    expect(snap!.savedAt).toBe(1_700_000_000_000);
    expect(snap!.items.map((i) => i.name)).toEqual(['Milk']);
    expect(snap!.items[0].category).toBe('Dairy');
  });

  it('reads null when nothing is stored', () => {
    expect(readSnapshot()).toBeNull();
    expect(hasSnapshot()).toBe(false);
  });

  it('hasSnapshot reflects presence, clearSnapshot empties it', () => {
    writeSnapshot([item({ id: 'a', name: 'Eggs' })]);
    expect(hasSnapshot()).toBe(true);
    clearSnapshot();
    expect(hasSnapshot()).toBe(false);
    expect(readSnapshot()).toBeNull();
  });

  it('reads null on a corrupt payload rather than throwing', () => {
    localStorage.setItem('trug_list_snapshot', '{not json');
    expect(readSnapshot()).toBeNull();
  });

  it('reads null when the shape is wrong (missing items array)', () => {
    localStorage.setItem('trug_list_snapshot', JSON.stringify({ savedAt: 1 }));
    expect(readSnapshot()).toBeNull();
  });
});

describe('formatAge', () => {
  it('reports sub-minute ages as moments ago', () => {
    expect(formatAge(30 * 1000)).toBe('moments ago');
  });
  it('pluralises minutes and hours', () => {
    expect(formatAge(60 * 1000)).toBe('1 minute ago');
    expect(formatAge(5 * 60 * 1000)).toBe('5 minutes ago');
    expect(formatAge(60 * 60 * 1000)).toBe('1 hour ago');
    expect(formatAge(3 * 60 * 60 * 1000)).toBe('3 hours ago');
  });
  it('rolls over to days past 24h', () => {
    expect(formatAge(25 * 60 * 60 * 1000)).toBe('1 day ago');
    expect(formatAge(3 * 24 * 60 * 60 * 1000)).toBe('3 days ago');
  });
});

describe('staleHint threshold', () => {
  const now = 2_000_000_000_000;

  it('is null once a refresh has reconciled, however old the snapshot', () => {
    expect(staleHint(now - 10 * SNAPSHOT_STALE_MS, true, now)).toBeNull();
  });

  it('is null when there is no snapshot', () => {
    expect(staleHint(null, false, now)).toBeNull();
  });

  it('is null while the snapshot is still fresh (under the threshold)', () => {
    expect(staleHint(now - (SNAPSHOT_STALE_MS - 1), false, now)).toBeNull();
  });

  it('surfaces "as of …" once past the threshold with no refresh yet', () => {
    const hint = staleHint(now - 2 * SNAPSHOT_STALE_MS, false, now);
    expect(hint).toBe('as of 2 hours ago');
  });
});
