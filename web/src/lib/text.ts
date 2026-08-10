/**
 * Client mirror of the server's `tidy_name` (trug/normalise.py): capitalise the
 * first letter of a word ONLY when it is entirely lowercase; words already
 * carrying an uppercase letter are left untouched ("BBQ sauce" → "BBQ Sauce",
 * "bbq" → "Bbq", "iPhone charger" → "iPhone Charger"). Applied to the optimistic
 * add so a freshly-added row reads the same as the server's stored name, with no
 * case-flash when the real row reconciles.
 */
export function tidyName(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .map((w) => (w === w.toLowerCase() ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(' ');
}

/**
 * Client mirror of the server's `normalise` (trug/normalise.py): lower-case and
 * collapse whitespace. This is the key the server dedups on, so it is also how
 * the store recognises that a name someone just typed is already on the list.
 *
 * The server additionally strips a trailing "s" when the singular is a known
 * word; that needs a catalogue the client doesn't carry, so this can miss a
 * plural the server would match. A miss is safe — it just falls back to the
 * ordinary add-and-reconcile path.
 */
export function normaliseName(name: string): string {
  return name.toLowerCase().split(/\s+/).filter(Boolean).join(' ');
}
