import { render, screen, waitFor } from '@testing-library/svelte';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import App from './App.svelte';
import { clearSnapshot } from './lib/snapshot';

// App wires AuthGate around AppShell. The point of the extraction is that
// AppShell's onMount — which opens the live-sync stream and refreshes the list —
// must NOT run until AuthGate has confirmed a session. These tests mock the
// side-effecting lib modules so we can assert exactly when live-sync fires.

const getToken = vi.fn();
const setToken = vi.fn();
const clearToken = vi.fn();
const probeToken = vi.fn();
const probeAuthStatus = vi.fn();
const consumeUrlTokenError = vi.fn();
const bootstrapState = vi.fn();
const apiList = vi.fn();
const apiTop = vi.fn();
const apiSearch = vi.fn();

vi.mock('./lib/api', () => ({
  ApiError: class ApiError extends Error {
    constructor(
      public status: number,
      message: string,
    ) {
      super(message);
    }
  },
  getToken: (...a: unknown[]) => getToken(...a),
  setToken: (...a: unknown[]) => setToken(...a),
  clearToken: (...a: unknown[]) => clearToken(...a),
  probeToken: (...a: unknown[]) => probeToken(...a),
  probeAuthStatus: (...a: unknown[]) => probeAuthStatus(...a),
  consumeUrlTokenError: (...a: unknown[]) => consumeUrlTokenError(...a),
  api: {
    list: (...a: unknown[]) => apiList(...a),
    top: (...a: unknown[]) => apiTop(...a),
    search: (...a: unknown[]) => apiSearch(...a),
    update: vi.fn(),
    auth: { bootstrapState: (...a: unknown[]) => bootstrapState(...a) },
  },
}));

vi.mock('./lib/passkey', () => ({
  isPasskeySupported: () => true,
  isCancellation: () => false,
  performRegistration: vi.fn(),
  performAuthentication: vi.fn(),
}));

// connectEvents is the thing that opens the SSE /api/events stream — spying it
// lets us assert whether live-sync started without needing a real EventSource.
const connectEvents = vi.fn((..._a: unknown[]) => () => {});
vi.mock('./lib/sse', () => ({ connectEvents: (...a: unknown[]) => connectEvents(...a) }));

// The store's refresh() is what issues GET /api/list. A fake store keeps the
// test off IndexedDB and lets us observe the fetch precisely.
let storeMock: ReturnType<typeof makeStore>;
function makeStore() {
  return {
    groups: [] as unknown[],
    checked: [] as unknown[],
    online: true,
    pendingIds: new Set<string>(),
    snapshotAt: null,
    refreshed: false,
    refresh: vi.fn(() => Promise.resolve()),
    retry: vi.fn(() => Promise.resolve()),
    dispose: vi.fn(),
    applyEvent: vi.fn(),
    add: vi.fn(),
    reorder: vi.fn(() => Promise.resolve()),
  };
}
vi.mock('./lib/store.svelte', () => ({
  createStore: () => storeMock,
  midpointSortKey: () => 0,
}));
vi.mock('./lib/drag.svelte', () => ({ createDragController: () => ({}) }));
vi.mock('./lib/opqueue', () => ({ OpQueue: class {} }));
vi.mock('./lib/storage', () => ({ requestPersistentStorage: vi.fn() }));

// Global EventSource / fetch spies — a literal backstop for "no /api/events and
// no /api/list request while sitting on the gate".
let esSpy: ReturnType<typeof vi.fn>;
let fetchSpy: ReturnType<typeof vi.fn>;

describe('App auth-gated live-sync', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearSnapshot();
    storeMock = makeStore();
    getToken.mockReturnValue(null);
    probeAuthStatus.mockResolvedValue('error');
    consumeUrlTokenError.mockReturnValue(false);
    bootstrapState.mockResolvedValue({ claimable: false });
    apiTop.mockResolvedValue([]);
    apiSearch.mockResolvedValue([]);
    apiList.mockResolvedValue({ items: [] });

    // jsdom lacks ResizeObserver, which the real add-bar/list children observe.
    globalThis.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;

    esSpy = vi.fn();
    fetchSpy = vi.fn(() => Promise.resolve({ ok: false, status: 401 } as Response));
    // @ts-expect-error test doubles for jsdom globals
    globalThis.EventSource = esSpy;
    globalThis.fetch = fetchSpy as unknown as typeof fetch;
  });

  it('does NOT open the live-sync stream or refresh the list while unauthenticated on the gate', async () => {
    // Genuinely signed out: no bearer, probe inconclusive/lost, no snapshot →
    // AuthGate shows the sign-in gate and AppShell never mounts.
    probeAuthStatus.mockResolvedValue('error');
    render(App);

    // The sign-in gate is up…
    await screen.findByRole('button', { name: /^sign in$/i });

    // …and crucially, live-sync never started: no SSE stream, no list refresh.
    // This is the regression guard for the unauthenticated reload loop.
    expect(connectEvents).not.toHaveBeenCalled();
    expect(storeMock.refresh).not.toHaveBeenCalled();
    expect(esSpy).not.toHaveBeenCalled();
    // No /api/events or /api/list issued via raw fetch either.
    for (const call of fetchSpy.mock.calls) {
      const url = String(call[0]);
      expect(url).not.toMatch(/\/api\/(events|list)/);
    }
  });

  it('opens the live-sync stream and refreshes the list once authenticated', async () => {
    getToken.mockReturnValue('tok');
    probeAuthStatus.mockResolvedValue('ok');
    render(App);

    // AppShell mounts behind the gate and starts live-sync.
    await waitFor(() => expect(connectEvents).toHaveBeenCalled());
    expect(storeMock.refresh).toHaveBeenCalled();
  });

  it('shows the machine-principal ownerless banner only for a bearer on a claimable instance', async () => {
    // Signed in with a machine bearer (cookieAuth false) on a zero-user instance.
    getToken.mockReturnValue('machine-tok');
    probeAuthStatus.mockResolvedValue('ok');
    bootstrapState.mockResolvedValue({ claimable: true });
    render(App);

    await waitFor(() => expect(connectEvents).toHaveBeenCalled());
    const banner = await screen.findByText(/this instance has no owner yet/i);
    expect(banner).toBeTruthy();
  });

  it('does NOT show the ownerless banner for a cookie (human) session', async () => {
    // No bearer → cookie session → cookieAuth true → banner suppressed even
    // though the instance is (impossibly, defensively) still claimable.
    getToken.mockReturnValue(null);
    probeAuthStatus.mockResolvedValue('ok');
    bootstrapState.mockResolvedValue({ claimable: true });
    render(App);

    await waitFor(() => expect(connectEvents).toHaveBeenCalled());
    expect(bootstrapState).not.toHaveBeenCalled();
    expect(screen.queryByText(/this instance has no owner yet/i)).toBeNull();
  });

  it('does NOT show the ownerless banner for a bearer once the instance is claimed', async () => {
    getToken.mockReturnValue('machine-tok');
    probeAuthStatus.mockResolvedValue('ok');
    bootstrapState.mockResolvedValue({ claimable: false });
    render(App);

    await waitFor(() => expect(connectEvents).toHaveBeenCalled());
    await waitFor(() => expect(bootstrapState).toHaveBeenCalled());
    expect(screen.queryByText(/this instance has no owner yet/i)).toBeNull();
  });
});
