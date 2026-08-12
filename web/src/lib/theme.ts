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
    localStorage.setItem(FLAVOUR_KEY, flavour);
  } else {
    delete root.dataset.flavour;
    localStorage.removeItem(FLAVOUR_KEY);
  }

  if (accent) {
    root.dataset.accent = accent;
    localStorage.setItem(ACCENT_KEY, accent);
  } else {
    delete root.dataset.accent;
    localStorage.removeItem(ACCENT_KEY);
  }
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
    localStorage.setItem(DENSITY_KEY, density);
  } else {
    delete root.dataset.density;
    localStorage.removeItem(DENSITY_KEY);
  }
}

/** Restore the persisted theme, if any, from localStorage. */
export function loadTheme(): void {
  applyTheme(
    localStorage.getItem(FLAVOUR_KEY),
    localStorage.getItem(ACCENT_KEY),
  );
  applyDensity(localStorage.getItem(DENSITY_KEY));
}
