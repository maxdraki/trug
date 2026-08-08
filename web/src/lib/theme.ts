const FLAVOUR_KEY = 'trug_flavour';
const ACCENT_KEY = 'trug_accent';

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

/** Restore the persisted theme, if any, from localStorage. */
export function loadTheme(): void {
  applyTheme(
    localStorage.getItem(FLAVOUR_KEY),
    localStorage.getItem(ACCENT_KEY),
  );
}
