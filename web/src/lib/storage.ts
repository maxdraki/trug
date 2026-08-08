/**
 * Ask the browser to keep our IndexedDB op-queue and localStorage snapshot from
 * being evicted under disk pressure. Fire-and-forget: installed PWAs usually
 * auto-grant, and a denied tab still works — it's just evictable — so neither
 * outcome surfaces UI. The last result is retained so Settings could later show
 * whether persistence is in effect.
 */

export type PersistResult = 'granted' | 'denied' | 'unsupported';

let lastResult: PersistResult | null = null;

/** The most recent {@link requestPersistentStorage} outcome, or null if unasked. */
export function storagePersisted(): PersistResult | null {
  return lastResult;
}

export async function requestPersistentStorage(): Promise<PersistResult> {
  if (
    typeof navigator === 'undefined' ||
    !navigator.storage ||
    typeof navigator.storage.persist !== 'function'
  ) {
    lastResult = 'unsupported';
    return lastResult;
  }
  try {
    lastResult = (await navigator.storage.persist()) ? 'granted' : 'denied';
  } catch {
    lastResult = 'denied';
  }
  return lastResult;
}
