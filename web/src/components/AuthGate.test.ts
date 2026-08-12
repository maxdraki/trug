import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { tick } from 'svelte';
import AuthGate from './AuthGate.svelte';
import { ApiError } from '../lib/api';
import { writeSnapshot, hasSnapshot, clearSnapshot } from '../lib/snapshot';
import type { Item } from '../lib/types';

const snapItem = { id: 'a', name: 'Milk', status: 'active' } as unknown as Item;

// AuthGate orchestrates the api + passkey glue; mock both so the state machine
// is tested without real network or navigator.credentials.
const getToken = vi.fn();
const setToken = vi.fn();
const clearToken = vi.fn();
const validateToken = vi.fn();
const hasCookieSession = vi.fn();
const probeAuthStatus = vi.fn();
const probeToken = vi.fn();
const consumeUrlTokenError = vi.fn();
const registerOptions = vi.fn();
const registerVerify = vi.fn();
const loginOptions = vi.fn();
const loginVerify = vi.fn();
const bootstrapState = vi.fn();
const bootstrapClaimOptions = vi.fn();
const bootstrapClaimVerify = vi.fn();

vi.mock('../lib/api', () => ({
  // Defined inline: vi.mock's factory is hoisted, so it cannot close over a
  // top-level declaration (same shape as App.test.ts).
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
  validateToken: (...a: unknown[]) => validateToken(...a),
  hasCookieSession: (...a: unknown[]) => hasCookieSession(...a),
  probeAuthStatus: (...a: unknown[]) => probeAuthStatus(...a),
  probeToken: (...a: unknown[]) => probeToken(...a),
  consumeUrlTokenError: (...a: unknown[]) => consumeUrlTokenError(...a),
  api: {
    auth: {
      registerOptions: (...a: unknown[]) => registerOptions(...a),
      registerVerify: (...a: unknown[]) => registerVerify(...a),
      loginOptions: (...a: unknown[]) => loginOptions(...a),
      loginVerify: (...a: unknown[]) => loginVerify(...a),
      bootstrapState: (...a: unknown[]) => bootstrapState(...a),
      bootstrapClaimOptions: (...a: unknown[]) => bootstrapClaimOptions(...a),
      bootstrapClaimVerify: (...a: unknown[]) => bootstrapClaimVerify(...a),
    },
  },
}));

const performRegistration = vi.fn();
const performAuthentication = vi.fn();
// The ceremony wrappers are stubbed (jsdom has no WebAuthn), but the pure
// predicates/constants come from the real module so the component's error
// mapping is tested against the classifications it will actually see.
vi.mock('../lib/passkey', async () => {
  const actual = await vi.importActual<typeof import('../lib/passkey')>('../lib/passkey');
  return {
    ...actual,
    isPasskeySupported: () => true,
    passkeySupport: () => 'ok',
    performRegistration: (...a: unknown[]) => performRegistration(...a),
    performAuthentication: (...a: unknown[]) => performAuthentication(...a),
  };
});

// A no-op children snippet: when the gate hands off (authed), it renders the
// snippet and its own form disappears — enough to prove the state transition.
const noopChildren = (() => {}) as any;

describe('AuthGate', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearSnapshot();
    getToken.mockReturnValue(null);
    hasCookieSession.mockResolvedValue(false);
    probeAuthStatus.mockResolvedValue('error');
    // Default: a pasted candidate token is CONFIRMED-invalid (a 401/403).
    // Individual token tests override to 'ok' (valid) or 'error' (network).
    probeToken.mockResolvedValue('lost');
    consumeUrlTokenError.mockReturnValue(false);
    // Default: the instance is already claimed, so a confirmed-invalid pasted
    // token stays "invalid" rather than diverting to the first-account bootstrap.
    bootstrapState.mockResolvedValue({ claimable: false });
  });

  it('hands to the app when a stored bearer still validates', async () => {
    getToken.mockReturnValue('tok-1');
    probeAuthStatus.mockResolvedValue('ok');
    render(AuthGate, { children: noopChildren });
    await waitFor(() => expect(probeAuthStatus).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /sign in/i })).toBeNull(),
    );
    expect(clearToken).not.toHaveBeenCalled();
  });

  it('clears a stale stored bearer and shows the gate on a confirmed 401/403', async () => {
    getToken.mockReturnValue('stale');
    probeAuthStatus.mockResolvedValue('lost');
    render(AuthGate, { children: noopChildren, initialSupported: true });
    await waitFor(() => expect(clearToken).toHaveBeenCalled());
    // Falls through to the sign-in gate rather than the app.
    expect(await screen.findByRole('button', { name: /^sign in$/i })).toBeTruthy();
  });

  it('keeps a stored bearer and hands to the app when boot probe only sees a network failure', async () => {
    getToken.mockReturnValue('tok-1');
    probeAuthStatus.mockResolvedValue('error');
    render(AuthGate, { children: noopChildren, initialSupported: true });
    await waitFor(() => expect(probeAuthStatus).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /sign in/i })).toBeNull(),
    );
    // A network blip is not a confirmed auth loss — the token survives.
    expect(clearToken).not.toHaveBeenCalled();
  });

  it('hands to the app when the cookie session probe passes', async () => {
    probeAuthStatus.mockResolvedValue('ok');
    render(AuthGate, { children: noopChildren });
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /sign in/i })).toBeNull(),
    );
  });

  it('clears the cached snapshot on a confirmed cookie auth loss (401/403)', async () => {
    writeSnapshot([snapItem], Date.now());
    probeAuthStatus.mockResolvedValue('lost');
    render(AuthGate, { children: noopChildren, initialSupported: true });
    // Falls through to the sign-in gate…
    await screen.findByRole('button', { name: /^sign in$/i });
    // …and the stale household list is gone, so the next boot can't flash it.
    expect(hasSnapshot()).toBe(false);
  });

  it('opens the app offline when a cookie boot sees a network error but a snapshot exists', async () => {
    writeSnapshot([snapItem], Date.now());
    probeAuthStatus.mockResolvedValue('error');
    render(AuthGate, { children: noopChildren, initialSupported: true });
    // Inconclusive network probe + evidence of a prior session → open the shelf.
    await waitFor(() => expect(screen.queryByRole('button', { name: /sign in/i })).toBeNull());
    expect(hasSnapshot()).toBe(true);
  });

  it('invite screen: creates a passkey and enters the app', async () => {
    registerOptions.mockResolvedValue({ challenge: 'c' });
    performRegistration.mockResolvedValue({ id: 'cred' });
    registerVerify.mockResolvedValue({ ok: true, user: 'alice' });

    render(AuthGate, { children: noopChildren, initialInvite: 'inv-123', initialSupported: true });

    const btn = await screen.findByRole('button', { name: /create your passkey/i });
    await fireEvent.click(btn);

    await waitFor(() => expect(registerOptions).toHaveBeenCalledWith('inv-123'));
    await waitFor(() => expect(registerVerify).toHaveBeenCalledWith('inv-123', { id: 'cred' }));
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /create your passkey/i })).toBeNull(),
    );
  });

  it('offers a "sign in instead" escape when an invite is spent', async () => {
    registerOptions.mockRejectedValue(new Error('Invalid or expired invite'));
    loginOptions.mockResolvedValue({ challenge: 'c' });
    performAuthentication.mockResolvedValue({ id: 'cred' });
    loginVerify.mockResolvedValue({ ok: true, user: 'alice' });

    render(AuthGate, { children: noopChildren, initialInvite: 'inv-x', initialSupported: true });

    await fireEvent.click(await screen.findByRole('button', { name: /create your passkey/i }));
    await screen.findByRole('alert'); // spent-invite error surfaces
    await fireEvent.click(await screen.findByRole('button', { name: /sign in instead/i }));

    // Now on the returning-user sign-in screen.
    expect(await screen.findByRole('button', { name: /^sign in$/i })).toBeTruthy();
  });

  it('sign-in screen: discoverable login enters the app', async () => {
    loginOptions.mockResolvedValue({ challenge: 'c' });
    performAuthentication.mockResolvedValue({ id: 'cred' });
    loginVerify.mockResolvedValue({ ok: true, user: 'alice' });

    render(AuthGate, { children: noopChildren, initialSupported: true });

    const btn = await screen.findByRole('button', { name: /^sign in$/i });
    await fireEvent.click(btn);

    await waitFor(() => expect(loginOptions).toHaveBeenCalled());
    await waitFor(() => expect(loginVerify).toHaveBeenCalledWith({ id: 'cred' }));
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /^sign in$/i })).toBeNull(),
    );
  });

  it('surfaces a server rejection on sign-in and stays gated', async () => {
    loginOptions.mockResolvedValue({ challenge: 'c' });
    performAuthentication.mockResolvedValue({ id: 'cred' });
    loginVerify.mockRejectedValue(new Error('Unknown credential'));

    render(AuthGate, { children: noopChildren, initialSupported: true });

    await fireEvent.click(await screen.findByRole('button', { name: /^sign in$/i }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Unknown credential');
    // Still gated.
    expect(screen.getByRole('button', { name: /^sign in$/i })).toBeTruthy();
  });

  it('offers the access-token fallback behind a quiet link on the sign-in screen', async () => {
    render(AuthGate, { children: noopChildren, initialSupported: true });

    // Hidden until the link is used.
    expect(screen.queryByLabelText('Access token')).toBeNull();
    await fireEvent.click(await screen.findByRole('button', { name: /use an access token/i }));
    expect(screen.getByLabelText('Access token')).toBeTruthy();
  });

  it('unsupported browser: shows the token path up front', async () => {
    render(AuthGate, { children: noopChildren, initialSupported: false });
    // No passkey buttons; token field is shown directly.
    expect(screen.queryByRole('button', { name: /^sign in$/i })).toBeNull();
    expect(await screen.findByLabelText('Access token')).toBeTruthy();
  });

  it('validates a pasted token before entering the app', async () => {
    probeToken.mockResolvedValue('ok');
    render(AuthGate, { children: noopChildren, initialSupported: false });

    const input = await screen.findByLabelText('Access token');
    await fireEvent.input(input, { target: { value: 'good' } });
    await fireEvent.click(screen.getByRole('button', { name: /open list/i }));

    await waitFor(() => expect(probeToken).toHaveBeenCalledWith('good'));
    await waitFor(() => expect(setToken).toHaveBeenCalledWith('good'));
    await waitFor(() => expect(screen.queryByLabelText('Access token')).toBeNull());
  });

  it('signs in a valid machine token on a still-claimable instance (no claim divert)', async () => {
    // A fresh instance is still claimable, but the pasted value is a REAL
    // machine token: it validates, so we must sign in — not divert to claim.
    probeToken.mockResolvedValue('ok');
    bootstrapState.mockResolvedValue({ claimable: true });
    render(AuthGate, { children: noopChildren, initialSupported: false });

    // Unclaimed instance — but this browser can't create a passkey, so the claim
    // is impossible here and the field stays labelled as the ordinary access
    // token it can still accept. A valid machine token must simply sign in.
    const input = await screen.findByLabelText('Access token');
    await fireEvent.input(input, { target: { value: 'machine-tok' } });
    await fireEvent.click(screen.getByRole('button', { name: /open list/i }));

    await waitFor(() => expect(setToken).toHaveBeenCalledWith('machine-tok'));
    await waitFor(() => expect(screen.queryByLabelText('Access token')).toBeNull());
    // Never diverted to the first-account claim panel. The gate probes
    // claimability once at boot; the point is that submitToken did NOT probe
    // again — a valid token short-circuits before the claim divert.
    expect(screen.queryByLabelText('Your name')).toBeNull();
    expect(bootstrapState).toHaveBeenCalledTimes(1);
  });

  it('surfaces "invalid token" for a confirmed-rejected token on an already-claimed instance', async () => {
    probeToken.mockResolvedValue('lost');
    bootstrapState.mockResolvedValue({ claimable: false });
    render(AuthGate, { children: noopChildren, initialSupported: true });

    await fireEvent.click(await screen.findByRole('button', { name: /use an access token/i }));
    const input = await screen.findByLabelText('Access token');
    await fireEvent.input(input, { target: { value: 'nope' } });
    await fireEvent.click(screen.getByRole('button', { name: /open list/i }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/wasn't accepted/i);
    expect(setToken).not.toHaveBeenCalled();
    expect(screen.queryByLabelText('Your name')).toBeNull();
  });

  it('first-run: a bootstrap token opens the create-first-account panel and claims it', async () => {
    // Zero-user instance: the pasted token is the bootstrap token — it's not a
    // machine token, so it 401s the probe ('lost') and the claimable instance
    // routes it into the first-account flow.
    probeToken.mockResolvedValue('lost');
    bootstrapState.mockResolvedValue({ claimable: true });
    bootstrapClaimOptions.mockResolvedValue({ challenge: 'c' });
    performRegistration.mockResolvedValue({ id: 'cred' });
    bootstrapClaimVerify.mockResolvedValue({ ok: true, user: 'alice' });

    render(AuthGate, { children: noopChildren, initialSupported: true });

    // The unclaimed gate offers the field up front — no "use an access token"
    // reveal needed, which is the whole point of the first-run treatment.
    const input = await screen.findByLabelText('Bootstrap token');
    await fireEvent.input(input, { target: { value: 'boot-secret' } });
    await fireEvent.click(screen.getByRole('button', { name: /open list/i }));

    // Switches to the name → passkey panel rather than validating as a token.
    const name = await screen.findByLabelText('Your name');
    await fireEvent.input(name, { target: { value: 'Alice' } });
    await fireEvent.click(screen.getByRole('button', { name: /create your passkey/i }));

    await waitFor(() => expect(bootstrapClaimOptions).toHaveBeenCalledWith('Alice', 'boot-secret'));
    await waitFor(() =>
      expect(bootstrapClaimVerify).toHaveBeenCalledWith('Alice', { id: 'cred' }, 'boot-secret'),
    );
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /create your passkey/i })).toBeNull(),
    );
    expect(validateToken).not.toHaveBeenCalled();
  });

  it('first-run: a network error during the token probe surfaces "unreachable", not "invalid token"', async () => {
    // The candidate-token probe can't reach the server ('error'). We must NOT
    // treat that as invalid (a lie about a possibly-good token) nor divert to
    // the claim panel — surface reachability and let them retry.
    probeToken.mockResolvedValue('error');

    render(AuthGate, { children: noopChildren, initialSupported: true });

    // Claimed instance (the default), so this is the ordinary hidden-fallback
    // token path — the reveal link, and an "access token" label.
    await fireEvent.click(await screen.findByRole('button', { name: /use an access token/i }));
    const input = await screen.findByLabelText('Access token');
    await fireEvent.input(input, { target: { value: 'boot-secret' } });
    await fireEvent.click(screen.getByRole('button', { name: /open list/i }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/couldn't reach the server/i);
    // Only the boot probe ran: an unreachable server must not be turned into a
    // claimability question, and must never divert into the claim panel.
    expect(bootstrapState).toHaveBeenCalledTimes(1);
    expect(alert.textContent).not.toMatch(/wasn't accepted/i);
    expect(screen.queryByLabelText('Your name')).toBeNull();
  });

  it('first-run: an already-claimed 403 mid-ceremony drops back to the normal gate', async () => {
    probeToken.mockResolvedValue('lost');
    // Models the race honestly: claimable at boot, then someone else claims it
    // mid-ceremony, so the re-probe after the 403 reports it gone.
    bootstrapState
      .mockResolvedValueOnce({ claimable: true })
      .mockResolvedValue({ claimable: false });
    bootstrapClaimOptions.mockResolvedValue({ challenge: 'c' });
    performRegistration.mockResolvedValue({ id: 'cred' });
    bootstrapClaimVerify.mockRejectedValue(
      Object.assign(new Error('Bootstrap already claimed'), { status: 403 }),
    );

    render(AuthGate, { children: noopChildren, initialSupported: true });

    const input = await screen.findByLabelText('Bootstrap token');
    await fireEvent.input(input, { target: { value: 'boot-secret' } });
    await fireEvent.click(screen.getByRole('button', { name: /open list/i }));

    const name = await screen.findByLabelText('Your name');
    await fireEvent.input(name, { target: { value: 'Alice' } });
    await fireEvent.click(screen.getByRole('button', { name: /create your passkey/i }));

    // No longer pinned in the claim panel — back on the returning-user gate,
    // with an explanation rather than a dead error.
    await screen.findByRole('button', { name: /^sign in$/i });
    expect(screen.queryByLabelText('Your name')).toBeNull();
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/claimed by someone else/i);
  });

  it('first-run: a "back to sign in" affordance escapes the claim panel', async () => {
    probeToken.mockResolvedValue('lost');
    bootstrapState.mockResolvedValue({ claimable: true });

    render(AuthGate, { children: noopChildren, initialSupported: true });

    const input = await screen.findByLabelText('Bootstrap token');
    await fireEvent.input(input, { target: { value: 'boot-secret' } });
    await fireEvent.click(screen.getByRole('button', { name: /open list/i }));

    // In the claim panel…
    await screen.findByLabelText('Your name');
    // …the escape hatch drops back to the normal gate.
    await fireEvent.click(screen.getByRole('button', { name: /back to sign in/i }));

    await screen.findByRole('button', { name: /^sign in$/i });
    expect(screen.queryByLabelText('Your name')).toBeNull();
  });

  describe('offline sign-in gate', () => {
    const setOnline = (value: boolean) => {
      Object.defineProperty(navigator, 'onLine', {
        configurable: true,
        get: () => value,
      });
      window.dispatchEvent(new Event(value ? 'online' : 'offline'));
    };

    afterEach(() => {
      // Restore the default so other suites see a connected navigator.
      Object.defineProperty(navigator, 'onLine', {
        configurable: true,
        get: () => true,
      });
    });

    it('gates sign-in when offline with no snapshot, then re-enables when back online', async () => {
      setOnline(false);
      // No stored bearer, no cookie session, no snapshot → the genuine
      // signed-out gate. probeAuthStatus 'error' + hasSnapshot() false.
      render(AuthGate, { children: noopChildren, initialSupported: true });

      const btn = await screen.findByRole('button', { name: /^sign in$/i });
      expect(btn).toHaveProperty('disabled', true);
      // The calm explanation is present.
      const status = await screen.findByRole('status');
      expect(status.textContent).toMatch(/offline/i);

      // Regaining connectivity re-enables the button automatically.
      setOnline(true);
      await waitFor(() => expect(btn).toHaveProperty('disabled', false));
    });
  });

  // OAuth consent round-trip: a `next` param must send the user back to
  // /oauth/authorize (or wherever they came from) once signed in — but only
  // to a safe, same-origin relative path. jsdom's location.assign doesn't
  // actually navigate ("Not implemented"), so location is swapped for a
  // spy-backed stand-in for the duration of these cases.
  describe('next-param redirect', () => {
    let assign: ReturnType<typeof vi.fn>;
    let originalLocation: Location;

    beforeEach(() => {
      assign = vi.fn();
      originalLocation = window.location;
      // @ts-expect-error jsdom's location can't be spied on directly.
      delete window.location;
      // @ts-expect-error swapping in a spy-backed stand-in for the test.
      window.location = { ...originalLocation, assign };
    });

    afterEach(() => {
      // @ts-expect-error restoring jsdom's real location object.
      window.location = originalLocation;
    });

    it('redirects immediately when already authenticated at boot with a safe next', async () => {
      probeAuthStatus.mockResolvedValue('ok');
      render(AuthGate, {
        children: noopChildren,
        initialNext: '/oauth/authorize?client_id=x',
      });
      await waitFor(() =>
        expect(assign).toHaveBeenCalledWith('/oauth/authorize?client_id=x'),
      );
    });

    it('does not redirect at boot when not authenticated', async () => {
      probeAuthStatus.mockResolvedValue('lost');
      render(AuthGate, {
        children: noopChildren,
        initialSupported: true,
        initialNext: '/oauth/authorize?client_id=x',
      });
      await screen.findByRole('button', { name: /^sign in$/i });
      expect(assign).not.toHaveBeenCalled();
    });

    it('redirects after a successful passkey sign-in', async () => {
      loginOptions.mockResolvedValue({ challenge: 'c' });
      performAuthentication.mockResolvedValue({ id: 'cred' });
      loginVerify.mockResolvedValue({ ok: true, user: 'alice' });

      render(AuthGate, {
        children: noopChildren,
        initialSupported: true,
        initialNext: '/oauth/authorize?client_id=x',
      });

      await fireEvent.click(await screen.findByRole('button', { name: /^sign in$/i }));

      await waitFor(() =>
        expect(assign).toHaveBeenCalledWith('/oauth/authorize?client_id=x'),
      );
    });

    it('redirects after a successful token fallback', async () => {
      probeToken.mockResolvedValue('ok');
      render(AuthGate, {
        children: noopChildren,
        initialSupported: false,
        initialNext: '/oauth/authorize?client_id=x',
      });

      const input = await screen.findByLabelText('Access token');
      await fireEvent.input(input, { target: { value: 'good' } });
      await fireEvent.click(screen.getByRole('button', { name: /open list/i }));

      await waitFor(() =>
        expect(assign).toHaveBeenCalledWith('/oauth/authorize?client_id=x'),
      );
    });

    it('never redirects to an unsafe next value even if one slips through', async () => {
      probeAuthStatus.mockResolvedValue('ok');
      render(AuthGate, {
        children: noopChildren,
        // AuthGate trusts its initialNext seed as already-resolved in tests,
        // so this only proves the wiring doesn't fire when next is absent;
        // the adversarial cases themselves are covered by safeNext's own
        // unit tests in nextParam.test.ts.
        initialNext: null,
      });
      await waitFor(() => expect(probeAuthStatus).toHaveBeenCalled());
      expect(assign).not.toHaveBeenCalled();
    });
  });
});

// ---------------------------------------------------------------------------
// First run: a freshly deployed, unclaimed instance
// ---------------------------------------------------------------------------

describe('AuthGate — unclaimed instance', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearSnapshot();
    getToken.mockReturnValue(null);
    hasCookieSession.mockResolvedValue(false);
    probeAuthStatus.mockResolvedValue('lost');
    probeToken.mockResolvedValue('lost');
    consumeUrlTokenError.mockReturnValue(false);
    bootstrapState.mockResolvedValue({ claimable: true });
  });

  it('greets a brand-new deployer instead of saying "welcome back"', async () => {
    render(AuthGate, { children: noopChildren, initialSupported: true });
    expect(await screen.findByText(/hasn't been claimed yet/i)).toBeTruthy();
    expect(screen.queryByText(/welcome back/i)).toBeNull();
  });

  it('leads with the bootstrap token field and drops the sign-in button that cannot work', async () => {
    render(AuthGate, { children: noopChildren, initialSupported: true });
    // The one action that can succeed is present up front, not behind a link…
    expect(await screen.findByLabelText(/bootstrap token/i)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /use an access token/i })).toBeNull();
    // …and the impossible one is gone (no account exists to sign in to).
    expect(screen.queryByRole('button', { name: /^sign in$/i })).toBeNull();
  });

  it('tells the deployer where to find the token', async () => {
    render(AuthGate, { children: noopChildren, initialSupported: true });
    // Scoped to the <code> element: the surrounding <p> also contains the
    // string, and the env var should be marked up as code, not prose.
    expect(await screen.findByText(/TRUG_BOOTSTRAP_TOKEN/, { selector: 'code' })).toBeTruthy();
    expect(screen.getByText(/container logs/i)).toBeTruthy();
  });

  it('shows the ordinary gate once the instance is claimed', async () => {
    bootstrapState.mockResolvedValue({ claimable: false });
    render(AuthGate, { children: noopChildren, initialSupported: true });
    expect(await screen.findByText(/welcome back/i)).toBeTruthy();
    expect(screen.queryByText(/hasn't been claimed yet/i)).toBeNull();
  });

  it('degrades to the ordinary gate when the claimable probe fails', async () => {
    // Offline or a 429 on the probe must never strand the sign-in screen.
    bootstrapState.mockRejectedValue(new Error('network'));
    render(AuthGate, { children: noopChildren, initialSupported: true });
    expect(await screen.findByRole('button', { name: /^sign in$/i })).toBeTruthy();
    expect(screen.queryByText(/hasn't been claimed yet/i)).toBeNull();
  });
});

// Hardening the first-run path: the claimable probe is best-effort, so every
// way it can fail must still leave a new deployer a usable route in.
describe('AuthGate — first-run probe failure modes', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearSnapshot();
    getToken.mockReturnValue(null);
    hasCookieSession.mockResolvedValue(false);
    probeAuthStatus.mockResolvedValue('lost');
    probeToken.mockResolvedValue('lost');
    consumeUrlTokenError.mockReturnValue(false);
    bootstrapState.mockResolvedValue({ claimable: false });
  });

  it('reveals the token field when the claimable probe fails, so a first-run deployer is not stranded', async () => {
    // If the probe fails on a genuinely unclaimed instance we show the ordinary
    // gate — whose "sign in" cannot work. The one action that CAN work must
    // therefore be on screen rather than hidden behind a link.
    bootstrapState.mockRejectedValue(new Error('network'));
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    render(AuthGate, { children: noopChildren, initialSupported: true });
    expect(await screen.findByLabelText('Access token')).toBeTruthy();
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it('does not re-probe claimability when the boot probe already said unclaimed', async () => {
    // Saves a round-trip and, more importantly, keeps the claim path off a
    // second chance to hit the probe's rate limit.
    bootstrapState.mockResolvedValue({ claimable: true });
    render(AuthGate, { children: noopChildren, initialSupported: true });
    const input = await screen.findByLabelText('Bootstrap token');
    await fireEvent.input(input, { target: { value: 'boot-secret' } });
    await fireEvent.click(screen.getByRole('button', { name: /open list/i }));
    await screen.findByLabelText('Your name');
    expect(bootstrapState).toHaveBeenCalledTimes(1);
  });

  it('says "too many attempts" — never "invalid token" — when the divert probe is rate limited', async () => {
    // A correct bootstrap token must never be reported as wrong just because
    // the probe was throttled; that reads as "my token is broken" on an
    // instance the deployer cannot otherwise get into.
    // Boot probe succeeds (so we're on the ordinary gate); the divert probe
    // inside submitToken is the one that gets throttled.
    bootstrapState
      .mockResolvedValueOnce({ claimable: false })
      .mockRejectedValue(new ApiError(429, 'Too many requests'));
    render(AuthGate, { children: noopChildren, initialSupported: true });
    await fireEvent.click(await screen.findByRole('button', { name: /use an access token/i }));
    const input = await screen.findByLabelText('Access token');
    await fireEvent.input(input, { target: { value: 'boot-secret' } });
    await fireEvent.click(screen.getByRole('button', { name: /open list/i }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/too many/i);
    expect(alert.textContent).not.toMatch(/wasn't accepted/i);
  });

  it('keeps the token field visible after escaping the claim panel', async () => {
    // "back to sign in" is a reversible-sounding label; it must not strip the
    // only affordance that works on a still-unclaimed instance.
    bootstrapState.mockResolvedValue({ claimable: true });
    render(AuthGate, { children: noopChildren, initialSupported: true });
    const input = await screen.findByLabelText('Bootstrap token');
    await fireEvent.input(input, { target: { value: 'boot-secret' } });
    await fireEvent.click(screen.getByRole('button', { name: /open list/i }));
    await screen.findByLabelText('Your name');
    await fireEvent.click(screen.getByRole('button', { name: /back to sign in/i }));
    await screen.findByRole('button', { name: /^sign in$/i });
    expect(screen.getByLabelText('Access token')).toBeTruthy();
  });

  it('marks the bootstrap field invalid for screen readers on the first-run gate', async () => {
    bootstrapState.mockResolvedValue({ claimable: true });
    probeToken.mockResolvedValue('error');
    render(AuthGate, { children: noopChildren, initialSupported: true });
    const input = await screen.findByLabelText('Bootstrap token');
    await fireEvent.input(input, { target: { value: 'x' } });
    await fireEvent.click(screen.getByRole('button', { name: /open list/i }));
    await screen.findByRole('alert');
    expect(screen.getByLabelText('Bootstrap token').getAttribute('aria-invalid')).toBe('true');
  });
});

// The claim endpoints answer 403 for three different causes (already claimed /
// wrong token / bad origin). Conflating them tells a deployer with a typo that
// someone else stole their instance — and, worse, tears down the first-run
// guidance that would help them fix it. (The genuine-race case is covered by
// the 'already-claimed 403 mid-ceremony' test above.)
describe('AuthGate — first-run 403 disambiguation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearSnapshot();
    getToken.mockReturnValue(null);
    hasCookieSession.mockResolvedValue(false);
    probeAuthStatus.mockResolvedValue('lost');
    probeToken.mockResolvedValue('lost');
    consumeUrlTokenError.mockReturnValue(false);
    bootstrapState.mockResolvedValue({ claimable: true });
    bootstrapClaimOptions.mockRejectedValue(
      Object.assign(new Error('Invalid bootstrap token'), { status: 403 }),
    );
  });

  async function reachClaimPanel() {
    render(AuthGate, { children: noopChildren, initialSupported: true });
    const input = await screen.findByLabelText('Bootstrap token');
    await fireEvent.input(input, { target: { value: 'typo-token' } });
    await fireEvent.click(screen.getByRole('button', { name: /open list/i }));
    const name = await screen.findByLabelText('Your name');
    await fireEvent.input(name, { target: { value: 'Alice' } });
    await fireEvent.click(screen.getByRole('button', { name: /create your passkey/i }));
  }

  it('keeps the first-run panel and blames the token when the instance is still unclaimed', async () => {
    await reachClaimPanel();
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/bootstrap token/i);
    // Must NOT claim someone else took it — on an unclaimed instance the
    // suggested recoveries (passkey, invite) are both impossible.
    expect(alert.textContent).not.toMatch(/someone else/i);
    // And the way out is still on screen.
    expect(screen.getByLabelText('Your name')).toBeTruthy();
  });
});

describe('AuthGate — inconclusive probe never accuses the token', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearSnapshot();
    getToken.mockReturnValue(null);
    hasCookieSession.mockResolvedValue(false);
    probeAuthStatus.mockResolvedValue('lost');
    probeToken.mockResolvedValue('lost');
    consumeUrlTokenError.mockReturnValue(false);
  });

  it('says "couldn\'t reach the server" when the divert probe 5xxs, not "invalid token"', async () => {
    // A warming proxy / restarting Pi 502s. The pasted token may be perfectly
    // correct — a 401 from /api/list is exactly what a correct BOOTSTRAP token
    // looks like — so claimability is unknown, not "your token is wrong".
    bootstrapState
      .mockResolvedValueOnce({ claimable: false })
      .mockRejectedValue(new ApiError(502, 'Bad gateway'));
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    render(AuthGate, { children: noopChildren, initialSupported: true });
    await fireEvent.click(await screen.findByRole('button', { name: /use an access token/i }));
    const input = await screen.findByLabelText('Access token');
    await fireEvent.input(input, { target: { value: 'boot-secret' } });
    await fireEvent.click(screen.getByRole('button', { name: /open list/i }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/couldn't reach the server/i);
    expect(alert.textContent).not.toMatch(/wasn't accepted/i);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  describe('honest error attribution', () => {
    // navigator.onLine gets stuck false on captive portals and missed events —
    // this file says so itself — so it must not be allowed to relabel errors
    // the server demonstrably answered.
    let restoreOnLine: (() => void) | null = null;
    afterEach(() => {
      restoreOnLine?.();
      restoreOnLine = null;
    });
    const goOffline = () => {
      const desc = Object.getOwnPropertyDescriptor(navigator, 'onLine');
      Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
      restoreOnLine = () => {
        if (desc) Object.defineProperty(navigator, 'onLine', desc);
        else delete (navigator as unknown as Record<string, unknown>).onLine;
      };
    };

    it('a server rejection is not reported as "you are offline"', async () => {
      // The real shape: online when the request goes out, and onLine reads false
      // by the time the answer comes back — a captive portal, or a missed event
      // that leaves the flag stuck. The server demonstrably replied.
      probeToken.mockResolvedValue('lost');
      bootstrapState.mockResolvedValue({ claimable: true });
      bootstrapClaimOptions.mockImplementation(async () => {
        goOffline();
        throw new ApiError(500, 'Something exploded server-side');
      });
      render(AuthGate, { children: noopChildren, initialSupported: true });

      const input = await screen.findByLabelText('Bootstrap token');
      await fireEvent.input(input, { target: { value: 'wrong-token' } });
      await fireEvent.click(screen.getByRole('button', { name: /open list/i }));
      const name = await screen.findByLabelText('Your name');
      await fireEvent.input(name, { target: { value: 'alice' } });
      await fireEvent.click(screen.getByRole('button', { name: /create your passkey/i }));

      const alert = await screen.findByRole('alert');
      expect(alert.textContent).not.toMatch(/you're offline/i);

    });

    it('names the config when the passkey fails to verify, instead of echoing the server', async () => {
      // The single most likely first-deploy failure: TRUG_ORIGIN/TRUG_RP_ID
      // disagree with the address the browser is on. The raw detail says
      // nothing a deployer can act on.
      probeToken.mockResolvedValue('lost');
      bootstrapState.mockResolvedValue({ claimable: true });
      bootstrapClaimOptions.mockResolvedValue({ challenge: 'c' });
      performRegistration.mockResolvedValue({ id: 'cred' });
      bootstrapClaimVerify.mockRejectedValue(new ApiError(400, 'Passkey verification failed'));
      render(AuthGate, { children: noopChildren, initialSupported: true });

      const input = await screen.findByLabelText('Bootstrap token');
      await fireEvent.input(input, { target: { value: 'boot' } });
      await fireEvent.click(screen.getByRole('button', { name: /open list/i }));
      const name = await screen.findByLabelText('Your name');
      await fireEvent.input(name, { target: { value: 'alice' } });
      await fireEvent.click(screen.getByRole('button', { name: /create your passkey/i }));

      const alert = await screen.findByRole('alert');
      expect(alert.textContent).toMatch(/trug status/i);
      expect(alert.textContent).toMatch(/address/i);
    });
  });

  // The pre-flight: name the reason a passkey can't be created here, and stop
  // starting a ceremony that cannot finish.
  describe('passkey pre-flight', () => {
    it('insecure context: blames the address, not the browser, and points at trug share', async () => {
      render(AuthGate, { children: noopChildren, initialBlocker: 'insecure-context' });
      await screen.findByLabelText('Access token');
      const body = document.body.textContent ?? '';
      expect(body).toMatch(/trug share/i);
      // The old copy blamed the device; on a LAN address the device is fine.
      expect(body).not.toMatch(/this browser (can't|doesn't)/i);
      // The "Not Secure" chip is expected here and shouldn't read as danger.
      expect(body).toMatch(/not secure/i);
      // The token path is still the way in, so the field is up front.
      expect(await screen.findByLabelText('Access token')).toBeTruthy();
      expect(screen.queryByRole('button', { name: /^sign in$/i })).toBeNull();
    });

    it('loopback by IP: the remedy is to open localhost instead', async () => {
      render(AuthGate, { children: noopChildren, initialBlocker: 'hostname-is-ip' });
      await screen.findByLabelText('Access token');
      const body = document.body.textContent ?? '';
      expect(body).toMatch(/localhost/i);
      expect(body).not.toMatch(/trug share/i);
    });

    it('a LAN IP over https is not told to open localhost, which would be the phone', async () => {
      render(AuthGate, { children: noopChildren, initialBlocker: 'hostname-is-lan-ip' });
      await screen.findByLabelText('Access token');
      const body = document.body.textContent ?? '';
      expect(body).toMatch(/needs a name/i);
      expect(body).not.toMatch(/open localhost/i);
    });

    it('unsupported browser: keeps the existing browser-blaming copy', async () => {
      render(AuthGate, { children: noopChildren, initialBlocker: 'unsupported' });
      await screen.findByLabelText('Access token');
      expect(document.body.textContent ?? '').toMatch(/doesn't support passkeys/i);
    });

    it('unclaimed instance: the address problem outranks "paste your bootstrap token"', async () => {
      // The trial's steady state — nobody spends the bootstrap token for weeks —
      // so this collision is the normal case, not an edge one.
      bootstrapState.mockResolvedValue({ claimable: true });
      render(AuthGate, { children: noopChildren, initialBlocker: 'insecure-context' });
      await waitFor(() => expect(bootstrapState).toHaveBeenCalled());
      const body = document.body.textContent ?? '';
      expect(body).toMatch(/trug share/i);
      expect(body).not.toMatch(/create the first account/i);
    });

    it('never starts a claim ceremony it cannot finish', async () => {
      // Pasting the bootstrap token on a blocked address used to enter the claim
      // panel and call performRegistration() into a void.
      probeToken.mockResolvedValue('lost');
      bootstrapState.mockResolvedValue({ claimable: true });
      render(AuthGate, { children: noopChildren, initialBlocker: 'insecure-context' });
      await waitFor(() => expect(bootstrapState).toHaveBeenCalled());

      const input = await screen.findByLabelText('Access token');
      await fireEvent.input(input, { target: { value: 'boot-secret' } });
      await fireEvent.click(screen.getByRole('button', { name: /open list/i }));

      const alert = await screen.findByRole('alert');
      expect(alert.textContent).toMatch(/passkey|address|share/i);
      expect(screen.queryByLabelText('Your name')).toBeNull();
      expect(performRegistration).not.toHaveBeenCalled();
    });

    it('keeps the invite in the URL on a blocked address, so it can be forwarded', async () => {
      // The remedy for a blocked address is "open this somewhere else". Stripping
      // #invite would destroy the very thing that has to be carried there.
      const replaceState = vi.spyOn(history, 'replaceState');
      render(AuthGate, {
        children: noopChildren,
        initialInvite: 'inv-123',
        initialBlocker: 'insecure-context',
      });
      await screen.findByLabelText('Access token');
      expect(replaceState).not.toHaveBeenCalled();
      replaceState.mockRestore();
    });

    it('still scrubs the invite when the browser itself is the problem', async () => {
      const replaceState = vi.spyOn(history, 'replaceState');
      render(AuthGate, {
        children: noopChildren,
        initialInvite: 'inv-123',
        initialBlocker: 'unsupported',
      });
      await screen.findByLabelText('Access token');
      expect(replaceState).toHaveBeenCalled();
      replaceState.mockRestore();
    });

    it('unsupported browser keeps a real next step, not just "paste a token"', async () => {
      render(AuthGate, { children: noopChildren, initialBlocker: 'unsupported' });
      await screen.findByLabelText('Access token');
      expect(document.body.textContent ?? '').toMatch(/Face ID|Touch ID|security key/i);
    });

    it('still signs in a valid machine token on a blocked address', async () => {
      // The whole point of the trial path: the list works here.
      probeToken.mockResolvedValue('ok');
      bootstrapState.mockResolvedValue({ claimable: true });
      render(AuthGate, { children: noopChildren, initialBlocker: 'insecure-context' });

      const input = await screen.findByLabelText('Access token');
      await fireEvent.input(input, { target: { value: 'mcp-tok' } });
      await fireEvent.click(screen.getByRole('button', { name: /open list/i }));
      await waitFor(() => expect(setToken).toHaveBeenCalledWith('mcp-tok'));
    });
  });
});

// A WebAuthn ceremony can simply never settle — a flaky security key, a
// biometric prompt stolen by something else, a device that sleeps mid-prompt.
// The gate used to sit on that unresolved promise forever, showing "creating…"
// with no timeout, no error and no retry. Every awaited ceremony must now be
// bounded and must recover WITHOUT losing what the user typed.
describe('AuthGate — a hung ceremony recovers', () => {
  let realPasskey: typeof import('../lib/passkey');
  let warn: ReturnType<typeof vi.spyOn>;

  beforeEach(async () => {
    vi.clearAllMocks();
    clearSnapshot();
    getToken.mockReturnValue(null);
    hasCookieSession.mockResolvedValue(false);
    probeAuthStatus.mockResolvedValue('lost');
    probeToken.mockResolvedValue('lost');
    consumeUrlTokenError.mockReturnValue(false);
    bootstrapState.mockResolvedValue({ claimable: true });
    bootstrapClaimOptions.mockResolvedValue(ceremonyOptions);
    registerOptions.mockResolvedValue(ceremonyOptions);
    loginOptions.mockResolvedValue({ challenge: 'AQID' });
    realPasskey = await vi.importActual<typeof import('../lib/passkey')>('../lib/passkey');
    warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    warn.mockRestore();
  });

  // Minimal well-formed server options: the real ceremony wrapper decodes them
  // before it ever touches the authenticator.
  const ceremonyOptions = {
    challenge: 'AQID',
    user: { id: 'AQI', name: 'alice', displayName: 'alice' },
    rp: { id: 'localhost', name: 'Trug' },
    pubKeyCredParams: [{ type: 'public-key', alg: -7 }],
    timeout: 60000,
  };

  /** A `navigator.credentials` whose ceremony never answers. */
  function hangingAuthenticator() {
    const create = vi.fn(() => new Promise<never>(() => {}));
    const get = vi.fn(() => new Promise<never>(() => {}));
    vi.stubGlobal('navigator', {
      onLine: true,
      credentials: { create, get },
      userAgent: 'test',
    });
    return { create, get };
  }

  /** Run the real (bounded) wrapper instead of the stub, for hang tests. */
  function useRealCeremonies() {
    performRegistration.mockImplementation((o: any) => realPasskey.performRegistration(o));
    performAuthentication.mockImplementation((o: any) => realPasskey.performAuthentication(o));
  }

  async function pastTheDeadline() {
    await vi.advanceTimersByTimeAsync(realPasskey.ceremonyDeadlineMs(ceremonyOptions) + 5000);
    await tick();
  }

  async function reachClaimPanel() {
    render(AuthGate, { children: noopChildren, initialSupported: true });
    const token = await screen.findByLabelText('Bootstrap token');
    await fireEvent.input(token, { target: { value: 'boot-secret' } });
    await fireEvent.click(screen.getByRole('button', { name: /open list/i }));
    const name = await screen.findByLabelText('Your name');
    await fireEvent.input(name, { target: { value: 'Alice' } });
  }

  it('unsticks the first-run claim, keeps the typed name, and can be pressed again', async () => {
    const { create } = hangingAuthenticator();
    useRealCeremonies();
    await reachClaimPanel();

    vi.useFakeTimers();
    await fireEvent.click(screen.getByRole('button', { name: /create your passkey/i }));
    expect(screen.getByRole('button', { name: /creating/i })).toBeTruthy();

    await pastTheDeadline();

    // Visible, actionable failure — not a permanent "creating…".
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toMatch(/didn't answer|try again/i);
    expect(alert.textContent).not.toMatch(/dismissed|cancel/i);
    // Resting state, with everything typed still in hand.
    const button = screen.getByRole('button', { name: /create your passkey/i }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    expect((screen.getByLabelText('Your name') as HTMLInputElement).value).toBe('Alice');

    // And a second press starts a genuinely fresh ceremony with the held token.
    await fireEvent.click(button);
    await vi.advanceTimersByTimeAsync(0);
    expect(create).toHaveBeenCalledTimes(2);
    expect(bootstrapClaimOptions).toHaveBeenLastCalledWith('Alice', 'boot-secret');
    expect(warn).toHaveBeenCalled();
  });

  it('unsticks a hung sign-in', async () => {
    hangingAuthenticator();
    useRealCeremonies();
    bootstrapState.mockResolvedValue({ claimable: false });
    render(AuthGate, { children: noopChildren, initialSupported: true });
    const button = await screen.findByRole('button', { name: /^sign in$/i });

    vi.useFakeTimers();
    await fireEvent.click(button);
    expect(screen.getByRole('button', { name: /signing in/i })).toBeTruthy();
    await pastTheDeadline();

    expect(screen.getByRole('alert').textContent).toMatch(/didn't answer|try again/i);
    expect((screen.getByRole('button', { name: /^sign in$/i }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it('unsticks a hung invite enrolment', async () => {
    hangingAuthenticator();
    useRealCeremonies();
    render(AuthGate, { children: noopChildren, initialInvite: 'inv-1', initialSupported: true });
    const button = await screen.findByRole('button', { name: /create your passkey/i });

    vi.useFakeTimers();
    await fireEvent.click(button);
    await pastTheDeadline();

    expect(screen.getByRole('alert').textContent).toMatch(/didn't answer|try again/i);
    expect(
      (screen.getByRole('button', { name: /create your passkey/i }) as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it('still reads as "you cancelled" when the user dismisses the prompt', async () => {
    // The two must never be conflated: blaming the user's own cancellation on a
    // timeout (or vice versa) sends them looking for the wrong problem.
    performRegistration.mockRejectedValue(new DOMException('x', 'NotAllowedError'));
    await reachClaimPanel();
    await fireEvent.click(screen.getByRole('button', { name: /create your passkey/i }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/dismissed/i);
    expect(alert.textContent).not.toMatch(/didn't answer/i);
  });

  it('names an authenticator that cannot make this passkey', async () => {
    performRegistration.mockRejectedValue(new DOMException('x', 'NotSupportedError'));
    await reachClaimPanel();
    await fireEvent.click(screen.getByRole('button', { name: /create your passkey/i }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/device|security key/i);
    expect(alert.textContent).not.toMatch(/didn't answer|dismissed/i);
  });

  it('points a browser-side origin mismatch at the address, not at the device', async () => {
    performRegistration.mockRejectedValue(new DOMException('x', 'SecurityError'));
    await reachClaimPanel();
    await fireEvent.click(screen.getByRole('button', { name: /create your passkey/i }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/address|origin/i);
  });
});
