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
