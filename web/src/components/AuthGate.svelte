<script lang="ts">
  import type { Snippet } from 'svelte';
  import { untrack } from 'svelte';
  import {
    getToken,
    setToken,
    clearToken,
    probeAuthStatus,
    probeToken,
    consumeUrlTokenError,
    api,
  } from '../lib/api';
  import { clearSnapshot, hasSnapshot } from '../lib/snapshot';
  import {
    isPasskeySupported,
    isCancellation,
    performRegistration,
    performAuthentication,
  } from '../lib/passkey';
  import { safeNext } from '../lib/nextParam';
  import Logo from '../lib/Logo.svelte';

  // `initialInvite`/`initialSupported` are one-shot test seeds (deliberately
  // read once, not tracked) so unit tests can drive each gate state without a
  // real location hash or navigator.
  let {
    children,
    initialInvite,
    initialSupported,
    initialNext,
  }: {
    children: Snippet;
    initialInvite?: string | null;
    initialSupported?: boolean;
    initialNext?: string | null;
  } = $props();

  // OAuth consent round-trip: the authorize endpoint bounces unauthenticated
  // users here as `/?next=<url-encoded /oauth/authorize?...>`. Once signed
  // in (by any path below), send them back — but only to a same-origin
  // relative path (see safeNext's open-redirect guard).
  function parseNext(): string | null {
    if (typeof location === 'undefined') return null;
    return safeNext(new URLSearchParams(location.search).get('next'));
  }

  const next = untrack(() => initialNext) ?? parseNext();

  function goNext() {
    if (next) location.assign(next);
  }

  const INVALID_TOKEN_MESSAGE = "That token wasn't accepted. Check it and try again.";
  const OFFLINE_MESSAGE =
    "you're offline. signing in needs a connection — your list will be here once you've signed in on wi-fi.";
  // The bootstrap probe itself failed to reach the server. We must NOT fall
  // through to validating the pasted token — on a fresh instance the pasted
  // value is the bootstrap token, which would be (correctly) rejected as
  // not-a-machine-token, lying "invalid token" when the real cause is a network
  // blip. Surface reachability and let them retry instead.
  const SERVER_UNREACHABLE_MESSAGE =
    "couldn't reach the server. check your connection and try again.";
  // The instance was claimed by someone else mid-ceremony — the held bootstrap
  // token is spent, so drop back to the normal gate.
  const ALREADY_CLAIMED_MESSAGE =
    'this instance was just claimed by someone else. sign in with your passkey or use an invite.';

  function parseInvite(): string | null {
    if (typeof location === 'undefined') return null;
    const hash = location.hash.startsWith('#') ? location.hash.slice(1) : location.hash;
    return new URLSearchParams(hash).get('invite');
  }

  const invite = untrack(() => initialInvite) ?? parseInvite();
  const supported = untrack(() => initialSupported) ?? isPasskeySupported();
  const storedToken = untrack(() => getToken());

  // Signed in when a stored bearer VALIDATES or the cookie probe passes. Both are
  // async; until they settle we hold on a quiet resolving state (unless an
  // invite branch owns the screen). A stored bearer is deliberately NOT trusted
  // sight-unseen — a stale one gets cleared below rather than handed to the app.
  // "Cleared" specifically means a CONFIRMED 401/403 (probeAuthStatus 'lost');
  // a network blip on boot must not destroy a token that's still good — it
  // resolves to 'error' and the app opens in an offline-ish state instead
  // (the list backfills once connectivity returns), mirroring how a fetch
  // failure elsewhere in the app degrades to offline rather than signing out.
  let authed = $state(false);
  let resolving = $state(!invite);

  // Set true from the invite dead-end ("sign in instead") to fall through to the
  // returning-user sign-in without a real invite in hand.
  let spent = $state(false);
  const showInvite = $derived(!!invite && !spent);

  let busy = $state(false);
  let error = $state<string | null>(consumeUrlTokenError() ? INVALID_TOKEN_MESSAGE : null);

  // Returning-user token fallback (machines/legacy): hidden behind a quiet link
  // on the sign-in screen, shown up front on unsupported browsers.
  let showToken = $state(!supported);
  let tokenValue = $state('');
  let checking = $state(false);

  // First-account bootstrap: when a pasted token is submitted on a still-empty
  // instance (server reports claimable), we hold it here and switch to a
  // "create the first account" panel — name → passkey → claim ceremony.
  let claiming = $state(false);
  let bootstrapToken = $state('');
  let firstName = $state('');

  // Passkey sign-in is a live challenge-response with the server, so it can't
  // work offline. Track connectivity reactively to disable the sign-in paths
  // and explain why, rather than letting a tap fail with a cryptic error. Guard
  // for SSR/absence; window listeners are cleaned up on destroy.
  let online = $state(typeof navigator === 'undefined' || navigator.onLine);
  $effect(() => {
    if (typeof window === 'undefined') return;
    const set = () => (online = navigator.onLine);
    window.addEventListener('online', set);
    window.addEventListener('offline', set);
    // navigator.onLine can get stuck false (captive portals, missed events).
    // Re-read whenever the user returns to the tab so a wrong offline reading
    // self-heals without a reload.
    document.addEventListener('visibilitychange', set);
    window.addEventListener('focus', set);
    return () => {
      window.removeEventListener('online', set);
      window.removeEventListener('offline', set);
      document.removeEventListener('visibilitychange', set);
      window.removeEventListener('focus', set);
    };
  });

  $effect(() => {
    if (!authed && resolving) {
      (async () => {
        if (storedToken) {
          // Validate a stored bearer before trusting it, but only clear it on a
          // CONFIRMED loss ('lost' — a 401/403). A network failure ('error')
          // is inconclusive: keep the token and let the app open anyway rather
          // than destroying a still-good credential over a boot-time blip.
          const status = await probeAuthStatus();
          if (status === 'lost') {
            clearToken();
            clearSnapshot();
          } else authed = true;
        } else {
          // Cookie session, tri-state so a cold offline boot still opens. 'ok'
          // signs in; a CONFIRMED 'lost' (401/403) drops the stale snapshot and
          // shows the gate. A network 'error' is inconclusive — proceed only
          // when a saved snapshot proves a prior signed-in session (so the
          // supermarket cold-start reads the last list), never for a genuinely
          // signed-out visitor with nothing on disk.
          const status = await probeAuthStatus();
          if (status === 'ok') authed = true;
          else if (status === 'lost') clearSnapshot();
          else authed = hasSnapshot();
        }
        resolving = false;
        if (authed) goNext();
      })();
    }
  });

  // Unsupported-invite dead-end: strip #invite so it doesn't linger in the URL
  // (mirrors the scrub the supported path does after a successful enrolment).
  $effect(() => {
    if (invite && !supported) scrubHash();
  });

  function scrubHash() {
    if (typeof history !== 'undefined') {
      history.replaceState(null, '', location.pathname + location.search);
    }
  }

  function friendly(err: unknown): string {
    // An attempt that failed while offline gets the plain explanation rather
    // than the network's cryptic error — the cause is simply no connection.
    if (typeof navigator !== 'undefined' && !navigator.onLine) return OFFLINE_MESSAGE;
    if (isCancellation(err)) return 'The passkey prompt was dismissed. Try again when ready.';
    if (err instanceof Error && err.message) return err.message;
    return 'Something went wrong. Try again.';
  }

  async function createPasskey() {
    if (busy || !invite) return;
    busy = true;
    error = null;
    try {
      const options = await api.auth.registerOptions(invite);
      const credential = await performRegistration(options);
      await api.auth.registerVerify(invite, credential);
      scrubHash();
      authed = true;
      goNext();
    } catch (err) {
      error = friendly(err);
    } finally {
      busy = false;
    }
  }

  async function signIn() {
    if (busy) return;
    busy = true;
    error = null;
    try {
      const options = await api.auth.loginOptions();
      const credential = await performAuthentication(options);
      await api.auth.loginVerify(credential);
      authed = true;
      goNext();
    } catch (err) {
      error = friendly(err);
    } finally {
      busy = false;
    }
  }

  async function submitToken(e: Event) {
    e.preventDefault();
    const token = tokenValue.trim();
    if (!token || checking) return;
    // Validation is a server round-trip, so the token path needs the network
    // too — explain rather than fail obscurely.
    if (!online) {
      error = OFFLINE_MESSAGE;
      return;
    }
    checking = true;
    error = null;
    // Validate the pasted token FIRST (tri-state), and only divert to the
    // first-account claim panel when it's a CONFIRMED-invalid token AND the
    // instance is still claimable. Diverting before validating would strand a
    // real machine/legacy token on a fresh instance in the claim flow.
    const status = await probeToken(token);
    if (status === 'ok') {
      // A genuine machine/legacy token — sign in.
      checking = false;
      setToken(token);
      authed = true;
      goNext();
      return;
    }
    if (status === 'error') {
      // Network — inconclusive. Do NOT treat as invalid (which would lie about a
      // still-good token) and do NOT divert to claim. Surface reachability.
      checking = false;
      error = SERVER_UNREACHABLE_MESSAGE;
      return;
    }
    // status === 'lost': the server CONFIRMED a 401/403. On a still-empty
    // instance the pasted value is the bootstrap token (not in settings.tokens,
    // so it correctly 401s /api/list) — switch to the create-first-account panel
    // and hold it. Anywhere else it's simply an invalid token.
    try {
      const state = await api.auth.bootstrapState();
      if (state.claimable) {
        bootstrapToken = token;
        claiming = true;
        checking = false;
        return;
      }
    } catch {
      // The claimability probe itself failed (network) — inconclusive. The
      // token was confirmed-rejected above, so fall through to "invalid" rather
      // than pinning them; a retry re-probes claimability.
    }
    checking = false;
    error = INVALID_TOKEN_MESSAGE;
  }

  // Leave the first-account claim panel and return to the normal gate, dropping
  // the held bootstrap token. The escape hatch out of claim limbo — used both by
  // the "back" affordance and automatically when the instance turns out to be
  // already claimed.
  function exitClaim() {
    claiming = false;
    bootstrapToken = '';
    firstName = '';
    error = null;
  }

  // Claim the first account: the held bootstrap token authorises a one-time
  // registration ceremony that creates user #1 (carrying the typed name) and
  // opens a session. If the instance was claimed by someone else mid-ceremony
  // the server 403s — the token is spent, so we drop back to the normal gate
  // (they can sign in with a passkey or use an invite) rather than pinning the
  // claim panel with a dead error. Other (retryable) errors keep the panel with
  // the "back" affordance as the manual escape.
  async function createFirstAccount() {
    const name = firstName.trim();
    if (busy || !name) return;
    if (!online) {
      error = OFFLINE_MESSAGE;
      return;
    }
    busy = true;
    error = null;
    try {
      const options = await api.auth.bootstrapClaimOptions(name, bootstrapToken);
      const credential = await performRegistration(options);
      await api.auth.bootstrapClaimVerify(name, credential, bootstrapToken);
      authed = true;
      goNext();
    } catch (err) {
      const status = (err as { status?: number } | null)?.status;
      if (status === 403) {
        exitClaim();
        error = ALREADY_CLAIMED_MESSAGE;
      } else {
        error = friendly(err);
      }
    } finally {
      busy = false;
    }
  }
</script>

{#if authed}
  {@render children()}
{:else}
  <div class="gate">
    <div class="col">
      <span class="mark"><Logo size={56} title="trug" /></span>
      <h1>trug</h1>

      {#if claiming}
        <!-- first-run: create the household's first account from a bootstrap token -->
        <p>welcome. you're the first here — pick a name and create a passkey to open your list.</p>
        {#if error}<p class="error" role="alert">{error}</p>{/if}
        {#if !online}<p class="offline" role="status">{OFFLINE_MESSAGE}</p>{/if}
        {#if supported}
          <form onsubmit={(e) => { e.preventDefault(); createFirstAccount(); }}>
            <input
              type="text"
              bind:value={firstName}
              placeholder="your name"
              aria-label="Your name"
              autocapitalize="words"
              autocomplete="name"
              oninput={() => (error = null)}
            />
            <button type="submit" class="primary" disabled={busy || !online || !firstName.trim()}>
              {busy ? 'creating…' : 'create your passkey'}
            </button>
          </form>
        {:else}
          <p>this browser can't create a passkey. open trug on a device with Face ID, Touch ID, or a security key to create the first account.</p>
        {/if}
        <!-- Escape hatch out of claim limbo: return to the normal gate (sign in
             with a passkey / use an invite) and drop the held bootstrap token. -->
        <button type="button" class="link" onclick={exitClaim} disabled={busy}>
          back to sign in
        </button>
      {:else if resolving}
        <p>Checking your session…</p>
      {:else if showInvite && supported}
        <!-- (a) invite in URL: one-tap enrolment -->
        <p>you're invited. create a passkey to open your list.</p>
        {#if error}
          <p class="error" role="alert">{error}</p>
          <!-- Dead-end escape: a spent/invalid invite still lets a returning
               user sign in with an existing passkey. -->
          <button type="button" class="link" onclick={() => (spent = true)}>
            sign in instead
          </button>
        {/if}
        {#if !online}<p class="offline" role="status">{OFFLINE_MESSAGE}</p>{/if}
        <button type="button" class="primary" onclick={createPasskey} disabled={busy || !online}>
          {busy ? 'creating…' : 'create your passkey'}
        </button>
      {:else if showInvite && !supported}
        <!-- invite but no passkey support: fall back to explaining -->
        <p>this browser can't create a passkey. open the invite on a device with Face ID, Touch ID, or a security key.</p>
        {#if error}<p class="error" role="alert">{error}</p>{/if}
      {:else if supported}
        <!-- (b) returning user: discoverable sign-in, quiet token fallback -->
        <p>welcome back.</p>
        {#if error}<p class="error" role="alert">{error}</p>{/if}
        {#if !online}<p class="offline" role="status">{OFFLINE_MESSAGE}</p>{/if}
        <button type="button" class="primary" onclick={signIn} disabled={busy || !online}>
          {busy ? 'signing in…' : 'sign in'}
        </button>
        {#if !showToken}
          <button type="button" class="link" onclick={() => (showToken = true)}>
            use an access token
          </button>
        {/if}
      {:else}
        <!-- (c) unsupported browser: token path with explanation -->
        <p>this browser doesn't support passkeys. paste an access token to open your list.</p>
      {/if}

      {#if !resolving && !claiming && showToken}
        <form onsubmit={submitToken}>
          {#if !online && !supported}<p class="offline" role="status">{OFFLINE_MESSAGE}</p>{/if}
          <input
            type="password"
            bind:value={tokenValue}
            placeholder="access token"
            aria-label="Access token"
            autocomplete="off"
            autocapitalize="off"
            aria-invalid={!!error && showToken}
            oninput={() => (error = null)}
          />
          <button type="submit" class="secondary" disabled={!tokenValue.trim() || checking || !online}>
            {checking ? 'checking…' : 'open list'}
          </button>
        </form>
      {/if}
    </div>
  </div>
{/if}

<style>
  .gate {
    min-height: 100svh;
    display: grid;
    place-items: center;
    padding: 24px;
    background: var(--ctp-mantle);
  }
  .col {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    width: 100%;
    max-width: 320px;
    text-align: center;
  }
  .mark {
    display: inline-flex;
    color: var(--accent);
  }
  h1 {
    margin: 0 0 4px;
    font-family: var(--font-display);
    font-size: 34px;
    font-weight: 500;
    letter-spacing: -0.03em;
    color: var(--ctp-text);
  }
  p {
    margin: 0 0 8px;
    color: var(--ctp-subtext0);
    font-size: 14px;
    line-height: 1.45;
  }
  .error {
    margin: -4px 0 0;
    color: var(--ctp-red);
    font-size: 13px;
  }
  .offline {
    margin: -4px 0 0;
    color: var(--ctp-subtext0);
    font-size: 13px;
  }
  form {
    display: flex;
    flex-direction: column;
    gap: 12px;
    width: 100%;
  }
  input {
    width: 100%;
    box-sizing: border-box;
    padding: 13px 16px;
    font-size: 16px;
    border-radius: var(--radius);
    border: var(--hairline);
    background: var(--ctp-base);
    color: var(--ctp-text);
  }
  input:focus {
    outline: none;
    border-color: var(--accent);
  }
  input[aria-invalid='true'] {
    border-color: var(--ctp-red);
  }
  button {
    width: 100%;
    padding: 13px;
    font-size: 15px;
    font-weight: 600;
    border: none;
    border-radius: var(--radius);
    cursor: pointer;
  }
  .primary {
    background: var(--accent);
    color: var(--ctp-crust);
  }
  .secondary {
    background: var(--ctp-surface0);
    color: var(--ctp-text);
  }
  button:disabled {
    opacity: 0.55;
  }
  .link {
    width: auto;
    padding: 4px;
    background: none;
    border: none;
    font-weight: 500;
    font-size: 13px;
    color: var(--ctp-subtext0);
    text-decoration: underline;
    text-underline-offset: 3px;
  }
  .link:hover {
    color: var(--ctp-text);
  }
  button:focus-visible,
  .link:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
</style>
