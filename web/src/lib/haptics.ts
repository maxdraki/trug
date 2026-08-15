/**
 * A short buzz when an item lands from somewhere other than this phone.
 *
 * WHY only machine sources: a person adding an item at the app already has the
 * feedback of their own tap, the row's arrival animation, and the keyboard
 * dismissing. A buzz there would be noise. The case worth a nudge is the one
 * with no visual context — the ring on someone's finger in the kitchen, or an
 * assistant adding items over MCP — where a household member holding the list
 * in the shop wants to feel that it just changed under them.
 *
 * WHY this is best-effort and must never be relied on:
 *
 * - The W3C Vibration API requires the document be *visible* AND have *sticky
 *   activation* before `vibrate()` does anything. Sticky activation means the
 *   user has interacted with the page at least once. So the first machine add
 *   after a cold page load will not buzz — the shelf has been opened but not
 *   touched. That is not a bug to chase; it is the spec's anti-annoyance rule.
 * - Firefox removed `navigator.vibrate` in version 129, and Safari never
 *   officially shipped it, so on those `vibrate` is simply undefined.
 * - Even where it exists, the UA may decline (silent mode, a battery saver) and
 *   return false.
 *
 * Everything here is therefore wrapped so that a failure to buzz can never
 * break list rendering: no throw escapes, and a missing API is a quiet no-op.
 */

export const HAPTICS_KEY = 'trug_haptics';

/** The one stored value. Absence — the common case — means enabled. */
const OFF = 'off';

/** 15ms: felt as a single tick, not a notification. */
const PULSE_MS = 15;

/** Sources that are not a human at this app. */
const MACHINE_SOURCES = new Set(['ring', 'mcp']);

function store(explicit?: Storage): Storage | null {
  // localStorage access itself throws in some privacy modes, before any
  // get/set — hence the guard around merely reaching for it.
  try {
    return explicit ?? localStorage;
  } catch {
    return null;
  }
}

/** Persisted per-device preference; defaults to true when unset. */
export function hapticsEnabled(storage?: Storage): boolean {
  try {
    return store(storage)?.getItem(HAPTICS_KEY) !== OFF;
  } catch {
    // Storage blocked: fall back to the default rather than silencing a
    // feature the shopper may have deliberately left on.
    return true;
  }
}

export function setHapticsEnabled(on: boolean, storage?: Storage): void {
  const s = store(storage);
  if (!s) return;
  try {
    if (on) s.removeItem(HAPTICS_KEY);
    else s.setItem(HAPTICS_KEY, OFF);
  } catch {
    // The preference cannot be remembered past this page (private browsing,
    // quota). The toggle still reflects the choice for this session.
  }
}

/** True for a source that is not a human at the app: 'ring' or 'mcp'. */
export function isMachineSource(source: string | null | undefined): boolean {
  return source != null && MACHINE_SOURCES.has(source);
}

/**
 * Pulse for a newly-arrived item if it came from a machine and haptics are on.
 * Returns true iff navigator.vibrate was actually called.
 */
export function tickOnRemoteAdd(
  item: { source: string | null },
  opts?: { navigator?: Pick<Navigator, 'vibrate'>; storage?: Storage },
): boolean {
  if (!isMachineSource(item.source)) return false;
  if (!hapticsEnabled(opts?.storage)) return false;

  const nav = opts?.navigator ?? (typeof navigator !== 'undefined' ? navigator : undefined);
  if (typeof nav?.vibrate !== 'function') return false;

  try {
    nav.vibrate(PULSE_MS);
    return true;
  } catch {
    // Some engines throw instead of returning false. A vibration that fails
    // must never break the render that triggered it.
    return false;
  }
}
