import { readStored, writeStored } from './safeStorage';

const FLAVOUR_KEY = 'trug_flavour';
const ACCENT_KEY = 'trug_accent';
const DENSITY_KEY = 'trug_density';

/** The one non-default density. Comfortable is `null` — no attribute, no key. */
export const DENSE = 'dense';

/**
 * Apply a Catppuccin flavour and accent to the document root and persist the
 * choice to localStorage. Passing `null` clears the corresponding attribute
 * (falling back to the CSS defaults) and removes it from storage.
 */
export function applyTheme(flavour: string | null, accent: string | null): void {
  const root = document.documentElement;

  if (flavour) {
    root.dataset.flavour = flavour;
  } else {
    delete root.dataset.flavour;
  }
  writeStored(FLAVOUR_KEY, flavour);

  if (accent) {
    root.dataset.accent = accent;
  } else {
    delete root.dataset.accent;
  }
  writeStored(ACCENT_KEY, accent);
}

/**
 * Apply a per-device list density to the document root and persist the choice
 * to localStorage. Passing `null` clears the attribute (falling back to the
 * comfortable CSS default) and removes it from storage.
 */
export function applyDensity(density: string | null): void {
  const root = document.documentElement;

  // Only 'dense' has a CSS block. Anything else — a stale key, a hand-edited
  // value — would render comfortable while leaving the settings sheet showing
  // neither density selected, so it is coerced back to the default.
  if (density !== null && density !== DENSE) {
    console.warn(`[trug] ignoring unknown density "${density}"; using comfortable`);
    density = null;
  }

  if (density) {
    root.dataset.density = density;
  } else {
    delete root.dataset.density;
  }
  writeStored(DENSITY_KEY, density);
}

/** Restore the persisted theme, if any, from localStorage. */
export function loadTheme(): void {
  applyTheme(readStored(FLAVOUR_KEY), readStored(ACCENT_KEY));
  applyDensity(readStored(DENSITY_KEY));
}
