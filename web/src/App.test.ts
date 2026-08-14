import { render, screen, waitFor } from '@testing-library/svelte';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
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
  passkeySupport: () => 'ok',
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

describe('App recents tray recovery', () => {
  function shelfItem(name: string): Record<string, unknown> {
    return {
      id: `id-${name}`,
      name,
      note: null,
      icon: null,
      category: null,
      status: 'active',
      source: 'pwa',
      added_by: null,
      created_at: '2026-08-09T10:00:00Z',
      checked_at: null,
      sort_key: 1,
    };
  }

  beforeEach(() => {
    vi.clearAllMocks();
    clearSnapshot();
    storeMock = makeStore();
    // The tray is suppressed on a completely empty shelf, so it needs a row to
    // render at all.
    storeMock.groups = [{ category: 'Cupboard', items: [shelfItem('Coffee')] }];
    getToken.mockReturnValue('tok');
    probeAuthStatus.mockResolvedValue('ok');
    consumeUrlTokenError.mockReturnValue(false);
    bootstrapState.mockResolvedValue({ claimable: false });
    apiSearch.mockResolvedValue([]);
    apiList.mockResolvedValue({ items: [] });
    globalThis.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
    // @ts-expect-error test double for jsdom global
    globalThis.EventSource = vi.fn();
    globalThis.fetch = vi.fn(() =>
      Promise.resolve({ ok: false, status: 401 } as Response),
    ) as unknown as typeof fetch;
  });

  it('refetches the catalogue on the FIRST stream connect, so a failed mount fetch recovers', async () => {
    // Launched with no signal: the tray's mount fetch fails and the tray is
    // blank. The first connect is precisely the moment the network arrived —
    // and it was the one connect that skipped the refetch, so the tray stayed
    // empty for the rest of the session unless an add or a delete happened.
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    apiTop.mockRejectedValueOnce(new Error('offline')).mockResolvedValue([]);
    render(App);

    await waitFor(() => expect(connectEvents).toHaveBeenCalled());
    await waitFor(() => expect(apiTop).toHaveBeenCalledTimes(1));

    const onConnect = connectEvents.mock.calls[0][2] as () => void;
    onConnect();
    await waitFor(() => expect(apiTop).toHaveBeenCalledTimes(2), { timeout: 4000 });
    err.mockRestore();
  });

  /* Which frames may have moved the catalogue, and how many refetches a burst
     of them costs. Both halves were unpinned: deleting the whole "only these
     three events" test left the suite green, and so did removing the
     coalescing so that every frame bumped. */

  /** The shell's own onEvent, as the SSE stream calls it. */
  function streamEvents(): (name: string, data?: unknown) => void {
    return connectEvents.mock.calls[0][0] as (name: string, data?: unknown) => void;
  }

  /** Render, and wait for the tray's mount fetch — the baseline every count
   *  below is measured from. */
  async function shellUp() {
    render(App);
    await waitFor(() => expect(connectEvents).toHaveBeenCalled());
    await waitFor(() => expect(apiTop).toHaveBeenCalledTimes(1));
    // Fake timers only now: swapped in before render they would freeze the
    // clock the mount path itself is waiting on. Everything from here is
    // driven by advancing them, so the second the coalescing window is meant
    // to be is asserted rather than slept through.
    vi.useFakeTimers();
  }

  afterEach(() => vi.useRealTimers());

  it('costs one refetch for a burst of adds, not one each', async () => {
    // A ring capture lands five names in a couple of seconds, and each one is
    // its own frame. Un-coalesced that is five catalogue GETs from a phone in a
    // carpark for a change the tray could not tell apart from one.
    await shellUp();
    const onEvent = streamEvents();
    // Spread across the window rather than fired in one breath: five frames in
    // a single synchronous batch collapse into one effect run whatever the
    // shell does, so that arrangement cannot tell coalescing from its absence.
    // A ring capture arrives the way this does — a name at a time, as they are
    // transcribed.
    for (const name of ['Lemon', 'Tuna', 'Coconut Milk', 'Coffee', 'Bananas']) {
      onEvent('item_added', { id: `id-${name}`, name, source: 'pwa' });
      await vi.advanceTimersByTimeAsync(200);
    }

    await vi.advanceTimersByTimeAsync(1000);
    expect(apiTop).toHaveBeenCalledTimes(2);
    // …and the window does not go on bumping after it has closed.
    await vi.advanceTimersByTimeAsync(5000);
    expect(apiTop).toHaveBeenCalledTimes(2);
  });

  it('costs nothing at all to check things off', async () => {
    // The promise this is: a check-off is the most repeated gesture in the app
    // and it cannot change the catalogue — neither can an edit, an uncheck, or
    // clearing the basket. Any of them bumping the revision would put a request
    // behind every tap of the walk round the shop.
    await shellUp();
    const onEvent = streamEvents();
    for (let i = 0; i < 5; i += 1) {
      onEvent('item_updated', { id: `id-${i}`, status: 'checked' });
    }
    onEvent('list_cleared', { cleared: 5 });

    await vi.advanceTimersByTimeAsync(5000);
    expect(apiTop).toHaveBeenCalledTimes(1);
  });

  it('refetches when a dropped stream comes back', async () => {
    // The stream carries nothing that happened while it was down, so a
    // reconnect is a moment the tray may be out of date — through the same
    // coalescing window as everything else.
    await shellUp();
    const onConnect = connectEvents.mock.calls[0][2] as () => void;
    onConnect();

    await vi.advanceTimersByTimeAsync(1000);
    expect(apiTop).toHaveBeenCalledTimes(2);
  });
});

// "Copy list" puts the outstanding items on the clipboard, one name per line in
// shelf display order — the shape supermarket multisearch boxes paste cleanly.
describe('App copy-list button', () => {
  let writeText: ReturnType<typeof vi.fn>;

  function makeItem(name: string, sortKey: number): Record<string, unknown> {
    return {
      id: `id-${name}`,
      name,
      note: null,
      icon: null,
      category: null,
      status: 'active',
      source: 'pwa',
      added_by: null,
      created_at: '2026-08-09T10:00:00Z',
      checked_at: null,
      sort_key: sortKey,
    };
  }

  beforeEach(() => {
    // Signed-in human so AppShell mounts (mocks reset by the outer beforeEach
    // don't apply across describes — repeat the auth arrangement here).
    vi.clearAllMocks();
    clearSnapshot();
    storeMock = makeStore();
    storeMock.groups = [
      { category: 'Fruit & Veg', items: [makeItem('Lemon', 1), makeItem('Bananas', 2)] },
      { category: 'Cupboard', items: [makeItem('Coffee', 1)] },
    ];
    getToken.mockReturnValue('tok');
    probeAuthStatus.mockResolvedValue('ok');
    consumeUrlTokenError.mockReturnValue(false);
    bootstrapState.mockResolvedValue({ claimable: false });
    apiTop.mockResolvedValue([]);
    apiSearch.mockResolvedValue([]);
    apiList.mockResolvedValue({ items: [] });
    globalThis.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
    // @ts-expect-error test double for jsdom global
    globalThis.EventSource = vi.fn();
    globalThis.fetch = vi.fn(() =>
      Promise.resolve({ ok: false, status: 401 } as Response),
    ) as unknown as typeof fetch;

    writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });
  });

  it('copies unchecked names newline-joined in shelf display order', async () => {
    render(App);
    const btn = await screen.findByRole('button', { name: /copy list/i });
    btn.click();
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('Lemon\nBananas\nCoffee'));
  });

  it('shows a "Copied 3 items" toast on success', async () => {
    render(App);
    const btn = await screen.findByRole('button', { name: /copy list/i });
    btn.click();
    expect(await screen.findByText(/copied 3 items/i)).toBeTruthy();
  });

  it('shows an honest failure toast when the clipboard write is blocked', async () => {
    writeText.mockRejectedValue(new Error('denied'));
    // The failure path deliberately console.warns for debuggability — keep the
    // test output clean and assert the trace exists.
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    render(App);
    const btn = await screen.findByRole('button', { name: /copy list/i });
    btn.click();
    expect(await screen.findByText(/couldn't copy/i)).toBeTruthy();
    expect(warn).toHaveBeenCalledWith('copy list failed', expect.any(Error));
    warn.mockRestore();
  });

  it('hides the button when there is nothing to copy', async () => {
    storeMock.groups = [];
    render(App);
    // Wait for AppShell to be up (the settings gear is its always-there chrome)…
    await screen.findByRole('button', { name: /settings/i });
    // …then assert the copy affordance is absent, not merely disabled.
    expect(screen.queryByRole('button', { name: /copy list/i })).toBeNull();
  });

  it('hides the button when the Clipboard API is unavailable (insecure context)', async () => {
    // Plain-HTTP LAN/Pi installs have no navigator.clipboard at all — the
    // button must not render as a permanently dead affordance there.
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    render(App);
    await screen.findByRole('button', { name: /settings/i });
    expect(screen.queryByRole('button', { name: /copy list/i })).toBeNull();
  });
});
