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
    ApiError,
  } from '../lib/api';
  import { clearSnapshot, hasSnapshot } from '../lib/snapshot';
  import {
    passkeySupport,
    isCancellation,
    isCeremonyTimeout,
    performRegistration,
    performAuthentication,
  } from '../lib/passkey';
  import type { PasskeySupport } from '../lib/passkey';
  import { safeNext } from '../lib/nextParam';
  import Logo from '../lib/Logo.svelte';

  // `initialInvite`/`initialSupported` are one-shot test seeds (deliberately
  // read once, not tracked) so unit tests can drive each gate state without a
  // real location hash or navigator.
  let {
    children,
    initialInvite,
    initialSupported,
    initialBlocker,
    initialNext,
  }: {
    children: Snippet;
    initialInvite?: string | null;
    initialSupported?: boolean;
    initialBlocker?: PasskeySupport;
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
  // The claimability probe was rate limited. Distinct from INVALID_TOKEN so a
  // correct bootstrap token is never reported as wrong just because we were
  // throttled — that reads as "my token is broken" to a first-time deployer.
  const THROTTLED_MESSAGE = 'too many attempts just now. wait a minute and try again.';
  // The instance was claimed by someone else mid-ceremony — the held bootstrap
  // token is spent, so drop back to the normal gate.
  const ALREADY_CLAIMED_MESSAGE =
    'this instance was just claimed by someone else. sign in with your passkey or use an invite.';
  // The claim was refused while the instance is still claimable — so it's the
  // token that's wrong, not the world. Kept distinct from ALREADY_CLAIMED so a
  // typo doesn't send a first-time deployer chasing a nonexistent passkey.
  const INVALID_BOOTSTRAP_TOKEN_MESSAGE =
    "that bootstrap token wasn't accepted. check it and try again.";

  function parseInvite(): string | null {
    if (typeof location === 'undefined') return null;
    const hash = location.hash.startsWith('#') ? location.hash.slice(1) : location.hash;
    return new URLSearchParams(hash).get('invite');
  }

  const invite = untrack(() => initialInvite) ?? parseInvite();
  // Why a passkey can't be created here, resolved once at boot. `initialSupported`
  // is the older boolean seed, kept so existing callers/tests keep working.
  const blocker: PasskeySupport = untrack(() => {
    if (initialBlocker) return initialBlocker;
    if (initialSupported === true) return 'ok';
    if (initialSupported === false) return 'unsupported';
    return passkeySupport();
  });
  const supported = blocker === 'ok';
  const storedToken = untrack(() => getToken());

  // What to say for each blocker. Every one of these used to render as "this
  // browser can't create a passkey", which is a misdiagnosis in two of the three
  // cases: on a home-network address the device is perfectly capable and the
  // *address* is the problem. Each line below has exactly one next step.
  const BLOCKED_COPY: Record<
    Exclude<PasskeySupport, 'ok'>,
    { lead: string; command?: string; hint: string }
  > = {
    'insecure-context': {
      lead: "you can use the list from here, but this address can't create an account — browsers only make passkeys over https or on localhost.",
      command: 'trug share',
      hint: 'run that on the machine trug is on, and open the link it prints. the "not secure" chip your browser shows is expected on a home-network address.',
    },
    'hostname-is-ip': {
      lead: "trug is running on this machine, but passkeys can't be made against an IP address.",
      hint: 'open localhost instead — same trug, same port.',
    },
    // Secure, so the connection is fine, but still an IP — someone put a proxy
    // or a certificate in front of a home-network address. `localhost` is no use
    // here: on the phone reading this, localhost is the phone.
    'hostname-is-lan-ip': {
      lead: "this address is an IP, and passkeys can't be made against one — even over https.",
      hint: 'the box needs a name. `tailscale serve` is the quickest, then `trug set-origin` with the name it prints. until then the list works from here, it just has no accounts.',
    },
    unsupported: {
      lead: "this browser doesn't support passkeys.",
      hint: 'paste an access token to open your list, or open trug on a device with Face ID, Touch ID, or a security key to create an account.',
    },
  };
  const blocked = supported ? null : BLOCKED_COPY[blocker as Exclude<PasskeySupport, 'ok'>];

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

  // A freshly deployed instance has no account to sign in to, so the ordinary
  // "welcome back / sign in" gate is a dead end there — the only action that
  // can succeed is pasting the bootstrap token. Resolved once at boot from the
  // server's claimable probe; a failed probe leaves this false and the gate
  // behaves exactly as before.
  let unclaimed = $state(false);

  // Whether to actually LEAD with the bootstrap token. An unclaimed instance
  // opened where no passkey can be created is still unclaimed, but the claim
  // can't be completed from here — so the address gets explained instead, and
  // the field stays labelled as the ordinary access token it can still accept.
  const claimPrompt = $derived(unclaimed && supported);

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
        // Only when we're actually about to show the gate: ask whether this
        // instance has been claimed, so a first-run visitor gets the claim
        // path instead of a sign-in button that cannot work. Best-effort —
        // offline, a 429, or any other failure just leaves the ordinary gate.
        if (!authed) {
          try {
            unclaimed = (await api.auth.bootstrapState()).claimable;
          } catch (err) {
            // Inconclusive: fall through to the returning-user gate, but REVEAL
            // the token field. If this instance really is unclaimed, "sign in"
            // cannot possibly work — leaving the only usable action hidden
            // behind a link would strand the deployer with no way in. Logged
            // because every cause lands here (offline, 429, 5xx, a renamed
            // export) and they are otherwise indistinguishable in the field.
            console.warn('claimable probe failed', err);
            showToken = true;
          }
        }
        resolving = false;
        if (authed) goNext();
      })();
    }
  });

  // Unsupported-invite dead-end: strip #invite so it doesn't linger in the URL
  // (mirrors the scrub the supported path does after a successful enrolment).
  //
  // Only for 'unsupported', NOT for the address-related blockers. Those fire on
  // exactly the address `trug share` hands out, and the remedy is to open the
  // invite somewhere else — so scrubbing it would destroy the one thing the
  // copy is asking them to forward, with no way to get it back.
  $effect(() => {
    if (invite && blocker === 'unsupported') scrubHash();
  });

  function scrubHash() {
    if (typeof history !== 'undefined') {
      history.replaceState(null, '', location.pathname + location.search);
    }
  }

  // A ceremony that never answered. Checked before everything else: our own
  // deadline fired, so nothing the server or the network says is relevant, and
  // the one useful instruction is "press it again".
  const CEREMONY_TIMEOUT_MESSAGE =
    "your device didn't answer. the passkey prompt may never have appeared — press the button to try again.";
  // The authenticator refused the *kind* of credential asked for. Nothing here
  // is retryable on this device, so point at a different one.
  const UNSUPPORTED_AUTHENTICATOR_MESSAGE =
    "this device can't make the kind of passkey trug asked for. try your phone, or a security key, or paste an access token instead.";
  // The browser (not the server) refused the RP ID — same root cause as the
  // server-side rejection below, caught one step earlier.
  const BAD_ORIGIN_MESSAGE =
    'your browser rejected the address trug is configured for. run `trug status` on the machine running trug, and fix what it names with `trug set-origin`.';
  // The authenticator already holds a passkey for this account.
  const ALREADY_ENROLLED_MESSAGE =
    'this device already has a passkey for this trug. sign in with it instead of making a new one.';

  function friendly(err: unknown): string {
    if (isCeremonyTimeout(err)) return CEREMONY_TIMEOUT_MESSAGE;
    if (err instanceof DOMException) {
      // Each of these is a different problem with a different next step;
      // collapsing them into one message throws that away.
      if (err.name === 'NotSupportedError') return UNSUPPORTED_AUTHENTICATOR_MESSAGE;
      if (err.name === 'SecurityError') return BAD_ORIGIN_MESSAGE;
      if (err.name === 'InvalidStateError') return ALREADY_ENROLLED_MESSAGE;
    }
    if (isCancellation(err)) return 'The passkey prompt was dismissed. Try again when ready.';
    // An ApiError is proof the server answered, so it is never an offline
    // problem however `navigator.onLine` feels about it. That flag gets stuck
    // false on captive portals and missed events (see the online listener
    // above), and relabelling a confirmed rejection as "you're offline" sends
    // someone to stand nearer the router while the button that would fix it is
    // disabled by the same stale flag.
    if (err instanceof ApiError) return apiFailure(err);
    // Anything else that failed while offline gets the plain explanation rather
    // than the network's cryptic error — the cause is simply no connection.
    if (typeof navigator !== 'undefined' && !navigator.onLine) return OFFLINE_MESSAGE;
    if (err instanceof Error && err.message) return err.message;
    return 'Something went wrong. Try again.';
  }

  // The server's ceremony-verification failure is the same 400 whether the
  // attestation was malformed or — far more often on a first deploy — the
  // server's TRUG_ORIGIN/TRUG_RP_ID don't match the address the browser is on.
  // The raw detail ("Passkey verification failed") is true and useless, so name
  // the likely cause and the one command that confirms it. Everything else
  // renders the server's own words, which are written for the person reading.
  function apiFailure(err: ApiError): string {
    if (err.status === 400 && /passkey verification failed/i.test(err.message)) {
      return (
        "the passkey was rejected by the server's settings, not by your device. " +
        'usually the address you are on and the origin trug is configured for ' +
        'disagree — run `trug status` on the machine running trug, and fix ' +
        'whatever it names with `trug set-origin`.'
      );
    }
    return err.message || 'Something went wrong. Try again.';
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
      console.warn('[trug] passkey enrolment failed', err);
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
      console.warn('[trug] passkey sign-in failed', err);
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
    // The boot probe already answered for the first-run gate, so trust it: no
    // second round-trip, and no second chance to hit the probe's rate limit on
    // the one path a new deployer depends on.
    let claimable = unclaimed;
    try {
      if (!claimable) claimable = (await api.auth.bootstrapState()).claimable;
    } catch (err) {
      // Throttled: say so. Reporting "that token wasn't accepted" here would be
      // a flat lie about a possibly-correct token, on an instance they may have
      // no other way into — the same reasoning as SERVER_UNREACHABLE_MESSAGE.
      console.warn('claimable probe failed during token submit', err);
      checking = false;
      // Claimability is UNKNOWN, so we cannot call this token invalid: a 401
      // from /api/list is exactly what a correct bootstrap token looks like on
      // a fresh instance. Say what actually went wrong instead. (Same reasoning
      // as SERVER_UNREACHABLE_MESSAGE above, reached via a different status.)
      error =
        err instanceof ApiError && err.status === 429
          ? THROTTLED_MESSAGE
          : SERVER_UNREACHABLE_MESSAGE;
      return;
    }
    checking = false;
    if (!claimable) {
      error = INVALID_TOKEN_MESSAGE;
      return;
    }
    // Claimable — so this is the bootstrap token. Divert to the claim panel,
    // unless no passkey can be created here: the panel's only action is a
    // registration ceremony, so from a blocked address that divert is the exact
    // dead end this pre-flight exists to close. Name the address instead.
    if (blocked) {
      error = `${blocked.lead} ${blocked.hint}`.trim();
      return;
    }
    bootstrapToken = token;
    claiming = true;
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
    // Also leave the first-run gate. Reached two ways, and it is the right
    // answer for both: after an "already claimed by someone else" 403 the
    // instance demonstrably IS claimed, so insisting otherwise would be a lie;
    // and a deliberate "back to sign in" is the manual override for a stale or
    // wrong probe. Pasting the bootstrap token again still routes to the claim.
    unclaimed = false;
    // …but keep the token field on screen. Leaving the first-run gate must not
    // strip the only affordance that works if the instance IS still unclaimed —
    // "back to sign in" reads as reversible, so it must not be a one-way
    // downgrade to a hidden link labelled for a different kind of token.
    showToken = true;
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
      // Nothing used to be said here at all — a hung or failed first-run claim
      // left an empty console for the one screen a new deployer sees first.
      console.warn('[trug] first-account claim failed', err);
      const status = (err as { status?: number } | null)?.status;
      if (status === 403) {
        // 403 covers three different causes: already claimed, wrong bootstrap
        // token, and a bad origin. Ask the server which world we're in rather
        // than assuming the worst — telling a deployer who mistyped their token
        // that "someone else claimed this instance" is a lie whose two
        // suggested recoveries (passkey, invite) are both impossible on an
        // unclaimed instance, and exitClaim would tear down the very guidance
        // that tells them where the real token lives.
        let stillClaimable: boolean | null = null;
        try {
          stillClaimable = (await api.auth.bootstrapState()).claimable;
        } catch (probeErr) {
          console.warn('claimable re-probe after 403 failed', probeErr);
        }
        if (stillClaimable === false) {
          exitClaim();
          error = ALREADY_CLAIMED_MESSAGE;
        } else {
          // Still claimable (or we couldn't tell): stay put, keep the first-run
          // context, and name the actual likely problem.
          error = INVALID_BOOTSTRAP_TOKEN_MESSAGE;
        }
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
        {:else if blocked}
          <p>{blocked.lead}</p>
          {#if blocked.command}<p><code>{blocked.command}</code></p>{/if}
        {#if blocked.hint}<p>{blocked.hint}</p>{/if}
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
      {:else if showInvite && blocked}
        <!-- invite, but this address or browser can't enrol one: name which -->
        <p>{blocked.lead}</p>
        {#if blocked.command}<p><code>{blocked.command}</code></p>{/if}
        {#if blocked.hint}<p>{blocked.hint}</p>{/if}
        {#if error}<p class="error" role="alert">{error}</p>{/if}
        {#if !online}<p class="offline" role="status">{OFFLINE_MESSAGE}</p>{/if}
      {:else if blocked}
        <!-- (a2) no passkey possible here. This outranks the unclaimed branch (a3)
             below: on a blocked address the bootstrap token cannot be spent, so
             leading with "paste it" would send someone into a ceremony that
             cannot finish. During a trial install this is the normal state for
             weeks, not an edge case. -->
        <p>{blocked.lead}</p>
        {#if blocked.command}<p><code>{blocked.command}</code></p>{/if}
        {#if blocked.hint}<p>{blocked.hint}</p>{/if}
        {#if error}<p class="error" role="alert">{error}</p>{/if}
        {#if !online}<p class="offline" role="status">{OFFLINE_MESSAGE}</p>{/if}
      {:else if unclaimed}
        <!-- (a3) first run: nobody has claimed this instance yet. There is no
             account to sign in to, so lead with the bootstrap token (the form
             below renders for this state) and answer "where do I find it?"
             right here — that question is the whole of the first-run cliff. -->
        <p class="claim-title">this trug hasn't been claimed yet.</p>
        <p>paste your bootstrap token to create the first account.</p>
        {#if error}<p class="error" role="alert">{error}</p>{/if}
        {#if !online}<p class="offline" role="status">{OFFLINE_MESSAGE}</p>{/if}
        <div class="where">
          <p>deployed on Railway? it's <code>TRUG_BOOTSTRAP_TOKEN</code> in your service's Variables tab.</p>
          <p>self-hosting with Docker? it's printed in the container logs on first boot.</p>
        </div>
      {:else}
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
      {/if}

      {#if !resolving && !claiming && (showToken || claimPrompt)}
        <form onsubmit={submitToken}>
          <input
            type="password"
            bind:value={tokenValue}
            placeholder={claimPrompt ? 'bootstrap token' : 'access token'}
            aria-label={claimPrompt ? 'Bootstrap token' : 'Access token'}
            autocomplete="off"
            autocapitalize="off"
            aria-invalid={!!error && (showToken || claimPrompt)}
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
  /* First-run welcome: calm, not an error state — this is the first thing a
     new deployer ever sees. The accent is spent on the mark above; peach text
     here reads as a warning (in Latte it is very nearly red), so the headline
     earns its emphasis from weight and the plain text colour instead. */
  .claim-title {
    color: var(--ctp-text);
    font-weight: 500;
  }
  .where {
    margin-top: 4px;
    font-size: 13px;
    line-height: 1.5;
    color: var(--ctp-subtext0);
  }
  .where p {
    margin: 0;
  }
  .where code {
    font-size: 12px;
    padding: 1px 4px;
    border-radius: 4px;
    background: var(--ctp-surface0);
    color: var(--ctp-subtext1);
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
