import type { Item } from './types';

/**
 * Last-known list snapshot, persisted so the shelf is readable on a cold start
 * with no signal (the founding requirement: "list readable with no signal").
 *
 * Storage is localStorage because lists are tiny and a synchronous read gives an
 * instant first paint — but every access goes through this module so the backing
 * store is swappable (IndexedDB/OPFS) without touching callers. All operations
 * are best-effort: a quota error, private-mode throw, or corrupt payload degrades
 * to "no snapshot" rather than crashing the boot path.
 */

const KEY = 'trug_list_snapshot';

/** Snapshot older than this, not yet reconciled with the server, is "stale". */
export const SNAPSHOT_STALE_MS = 60 * 60 * 1000;

export interface Snapshot {
  /** All active + checked rows at persist time, flattened. */
  items: Item[];
  /** Epoch ms when this snapshot was captured. */
  savedAt: number;
}

export function readSnapshot(): Snapshot | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (
      !parsed ||
      typeof parsed !== 'object' ||
      !Array.isArray((parsed as Snapshot).items) ||
      typeof (parsed as Snapshot).savedAt !== 'number'
    ) {
      return null;
    }
    return parsed as Snapshot;
  } catch {
    return null;
  }
}

export function writeSnapshot(items: Item[], savedAt: number = Date.now()): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ items, savedAt } satisfies Snapshot));
  } catch {
    /* quota / private mode — the snapshot is a best-effort cache. */
  }
}

export function clearSnapshot(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

/** Whether a snapshot is on disk — evidence of a prior signed-in session. */
export function hasSnapshot(): boolean {
  try {
    return localStorage.getItem(KEY) != null;
  } catch {
    return false;
  }
}

/** Human, lowercase-leaning age: "moments ago", "5 minutes ago", "2 hours ago". */
export function formatAge(ageMs: number): string {
  const mins = Math.floor(ageMs / 60000);
  if (mins < 1) return 'moments ago';
  if (mins < 60) return `${mins} minute${mins === 1 ? '' : 's'} ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
}

/**
 * Copy for the quiet "as of …" hint shown near the header when the shelf is
 * rendering from a snapshot that has NOT yet been reconciled with the server and
 * is older than {@link SNAPSHOT_STALE_MS}. Returns null when there's nothing to
 * say: already refreshed this session, no snapshot, or still fresh.
 */
export function staleHint(
  savedAt: number | null,
  refreshed: boolean,
  now: number = Date.now(),
): string | null {
  if (refreshed || savedAt == null) return null;
  const age = now - savedAt;
  if (age < SNAPSHOT_STALE_MS) return null;
  return `as of ${formatAge(age)}`;
}
