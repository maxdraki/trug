import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// main.ts is a side-effect module (reads location.search, mounts <App/> at
// import time), so every test re-imports it fresh after arranging the URL
// and the api mocks it should observe.
const setToken = vi.fn();
const validateToken = vi.fn();
const markUrlTokenInvalid = vi.fn();
const mount = vi.fn(() => ({}));

vi.mock('./lib/api', () => ({ setToken, validateToken, markUrlTokenInvalid }));
vi.mock('./lib/theme', () => ({ loadTheme: vi.fn() }));
vi.mock('svelte', () => ({ mount }));
vi.mock('./App.svelte', () => ({ default: {} }));
// The PWA register virtual module is only materialised by vite-plugin-pwa at
// build time, so stub it for the unit test.
vi.mock('virtual:pwa-register', () => ({ registerSW: vi.fn() }));

async function loadMainAt(url: string) {
  window.history.replaceState(null, '', url);
  vi.resetModules();
  return import('./main');
}

describe('main.ts URL token bootstrap', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="app"></div>';
    setToken.mockReset();
    validateToken.mockReset();
    markUrlTokenInvalid.mockReset();
    mount.mockClear();
  });

  afterEach(() => {
    window.history.replaceState(null, '', '/');
  });

  it('does nothing token-related when opened with no ?token=', async () => {
    await loadMainAt('/');
    // Flush the async init().
    await new Promise((r) => setTimeout(r, 0));

    expect(validateToken).not.toHaveBeenCalled();
    expect(setToken).not.toHaveBeenCalled();
    expect(mount).toHaveBeenCalled();
  });

  it('strips ?token= from the URL immediately, before validation resolves', async () => {
    validateToken.mockReturnValue(new Promise(() => {})); // never resolves
    await loadMainAt('/?token=abc123');

    expect(location.search).toBe('');
  });

  it('stores the token and mounts once validation succeeds', async () => {
    validateToken.mockResolvedValue(true);
    await loadMainAt('/?token=good-token');
    await new Promise((r) => setTimeout(r, 0));

    expect(validateToken).toHaveBeenCalledWith('good-token');
    expect(setToken).toHaveBeenCalledWith('good-token');
    expect(markUrlTokenInvalid).not.toHaveBeenCalled();
    expect(mount).toHaveBeenCalled();
  });

  it('never stores a token that fails validation, and flags it for the gate instead', async () => {
    validateToken.mockResolvedValue(false);
    await loadMainAt('/?token=garbage');
    await new Promise((r) => setTimeout(r, 0));

    expect(validateToken).toHaveBeenCalledWith('garbage');
    expect(setToken).not.toHaveBeenCalled();
    expect(markUrlTokenInvalid).toHaveBeenCalled();
    // The app still mounts — AuthGate is what surfaces the error.
    expect(mount).toHaveBeenCalled();
  });
});
