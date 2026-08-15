/**
 * `localStorage` that cannot take the app down with it.
 *
 * In a browser with site data blocked — Safari's "Prevent cross-site tracking"
 * on an embedded context, Firefox's strictest mode, Chrome with cookies
 * disabled — `window.localStorage` is a getter that THROWS. Not `getItem`: the
 * lookup itself. So `try { localStorage.getItem(k) } catch {}` guards the wrong
 * thing only by accident, and `if (localStorage)` raises before it can test
 * anything. Every access has to sit inside the try, including naming it.
 *
 * That mattered more than it sounds. `loadTheme()` runs at `main.ts:12`, before
 * the app mounts, and read three keys unguarded; `getToken()` is on the path of
 * every request. Either one throwing meant a blank page — not a lost preference,
 * no shopping list at all — for the users who have most deliberately locked
 * their browser down.
 *
 * A preference we cannot read is not an error. It is a preference that falls
 * back to its default, which is exactly what these two functions express.
 */

/** The stored string, or null if absent — or if storage cannot be reached. */
export function readStored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** Store a value, or remove the key when `value` is null. Never throws. */
export function writeStored(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // Blocked or over quota. The caller's in-memory state is already correct;
    // it simply will not survive a reload, which is the right trade against
    // failing the interaction that set it.
  }
}
