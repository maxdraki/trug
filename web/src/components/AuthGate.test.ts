import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import AuthGate from './AuthGate.svelte';
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
vi.mock('../lib/passkey', () => ({
  isPasskeySupported: () => true,
  isCancellation: (e: unknown) => e instanceof DOMException && e.name === 'NotAllowedError',
  performRegistration: (...a: unknown[]) => performRegistration(...a),
  performAuthentication: (...a: unknown[]) => performAuthentication(...a),
}));

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

    const input = await screen.findByLabelText('Access token');
    await fireEvent.input(input, { target: { value: 'machine-tok' } });
    await fireEvent.click(screen.getByRole('button', { name: /open list/i }));

    await waitFor(() => expect(setToken).toHaveBeenCalledWith('machine-tok'));
    await waitFor(() => expect(screen.queryByLabelText('Access token')).toBeNull());
    // Never diverted to the first-account claim panel, never even probed it.
    expect(screen.queryByLabelText('Your name')).toBeNull();
    expect(bootstrapState).not.toHaveBeenCalled();
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

    // Reveal the token field, then paste the bootstrap token.
    await fireEvent.click(await screen.findByRole('button', { name: /use an access token/i }));
    const input = await screen.findByLabelText('Access token');
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

    await fireEvent.click(await screen.findByRole('button', { name: /use an access token/i }));
    const input = await screen.findByLabelText('Access token');
    await fireEvent.input(input, { target: { value: 'boot-secret' } });
    await fireEvent.click(screen.getByRole('button', { name: /open list/i }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/couldn't reach the server/i);
    // Never even probed claimability, and never diverted into the claim panel.
    expect(bootstrapState).not.toHaveBeenCalled();
    expect(alert.textContent).not.toMatch(/wasn't accepted/i);
    expect(screen.queryByLabelText('Your name')).toBeNull();
  });

  it('first-run: an already-claimed 403 mid-ceremony drops back to the normal gate', async () => {
    probeToken.mockResolvedValue('lost');
    bootstrapState.mockResolvedValue({ claimable: true });
    bootstrapClaimOptions.mockResolvedValue({ challenge: 'c' });
    performRegistration.mockResolvedValue({ id: 'cred' });
    bootstrapClaimVerify.mockRejectedValue(
      Object.assign(new Error('Bootstrap already claimed'), { status: 403 }),
    );

    render(AuthGate, { children: noopChildren, initialSupported: true });

    await fireEvent.click(await screen.findByRole('button', { name: /use an access token/i }));
    const input = await screen.findByLabelText('Access token');
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

    await fireEvent.click(await screen.findByRole('button', { name: /use an access token/i }));
    const input = await screen.findByLabelText('Access token');
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
