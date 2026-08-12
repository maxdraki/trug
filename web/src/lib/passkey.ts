/**
 * WebAuthn client glue: the small amount of encode/decode + navigator plumbing
 * between the server's ceremony JSON and the browser's credential APIs.
 *
 * The server speaks base64url (py_webauthn's `options_to_json_dict`), while
 * `navigator.credentials.{create,get}` speak ArrayBuffers. This module is the
 * codec + shape translation across that boundary. It is hand-rolled (~one file,
 * no dependency) rather than pulling in @simplewebauthn/browser: the surface we
 * touch is tiny and stable, the codec is worth unit-testing directly, and the
 * offline PWA keeps a leaner bundle. The tricky WebAuthn edge cases
 * @simplewebauthn shields you from (attestation formats, conditional UI) aren't
 * in play here — two seeded users, discoverable credentials, platform
 * authenticators.
 */

// --- base64url codec (RFC 4648 §5, unpadded) ---------------------------------

/** Decode an unpadded base64url string to bytes. */
export function base64urlToBytes(value: string): Uint8Array<ArrayBuffer> {
  const padded = value.length % 4 === 0 ? value : value + '='.repeat(4 - (value.length % 4));
  const binary = atob(padded.replace(/-/g, '+').replace(/_/g, '/'));
  // Back the view with a concrete ArrayBuffer so it satisfies BufferSource
  // (WebAuthn option fields) under TS's typed-array generics.
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

/** Encode bytes as an unpadded base64url string. */
export function bytesToBase64url(input: ArrayBuffer | Uint8Array): string {
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
  let binary = '';
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

// --- feature detection -------------------------------------------------------

/**
 * Why a passkey can't be created here — or `'ok'` when one can.
 *
 * A bare boolean sent everyone to the same dead end ("this browser can't create
 * a passkey") when the browser was usually fine and the *address* was the
 * problem. Each reason below has a different, one-step remedy, so they are
 * reported separately.
 */
export type PasskeySupport =
  | 'ok'
  | 'insecure-context'
  | 'hostname-is-ip'
  | 'hostname-is-lan-ip'
  | 'unsupported';

/**
 * True when the hostname is an IP literal rather than a name. Brackets are
 * stripped first: browsers report an IPv6 host as `[::1]`, brackets included.
 * A colon can only be an IPv6 separator here — `location.hostname` never
 * carries the port.
 */
function bareHost(hostname: string): string {
  return hostname.replace(/^\[|\]$/g, '');
}

function isIpLiteral(hostname: string): boolean {
  const bare = bareHost(hostname);
  return /^\d{1,3}(\.\d{1,3}){3}$/.test(bare) || bare.includes(':');
}

/** True for an address that means *this* machine — so typing `localhost` fixes it. */
function isLoopback(hostname: string): boolean {
  const bare = bareHost(hostname);
  return /^127\./.test(bare) || bare === '::1' || bare === '0:0:0:0:0:0:0:1';
}

/**
 * Diagnose the passkey path *before* starting a ceremony, so the gate can name
 * the actual cause instead of calling into a void.
 *
 * Order matters. A plain-http LAN address is both insecure and an IP, but only
 * `insecure-context` has the right remedy (use the share link — the address
 * can't be renamed into working), so it is checked first. What's left after
 * that is loopback-by-IP: a secure context, so the ceremony starts, and then
 * dies on the RP ID because WebAuthn forbids an IP there. That one is fixed by
 * typing `localhost`.
 *
 * The RP ID itself is deliberately not consulted: the client cannot know the
 * server's `TRUG_RP_ID` before it asks for ceremony options, so this checks
 * only what the page can see about itself.
 */
export function passkeySupport(): PasskeySupport {
  if (typeof window === 'undefined' || typeof navigator === 'undefined') return 'unsupported';
  // Secure context first, and it has to be: `PublicKeyCredential` is exposed
  // only in a secure context, so an insecure page ALSO has no WebAuthn. Testing
  // capability first would label every home-network address "unsupported" and
  // lose the one message that tells people what to do about it.
  if (window.isSecureContext === false) return 'insecure-context';
  // Secure and still no WebAuthn: now it genuinely is the browser. Checked
  // before the address, because no address change fixes a missing API — telling
  // such a browser to "open localhost instead" spends its only message on
  // advice that cannot work.
  if (typeof window.PublicKeyCredential !== 'function' || !navigator.credentials) {
    return 'unsupported';
  }
  const hostname = window.location?.hostname ?? '';
  if (hostname && isIpLiteral(hostname)) {
    // Loopback is a typo with a one-word fix. Any other IP is a secure context
    // reached over https on the network — a reverse proxy on a LAN address —
    // where "open localhost" would point the phone at itself.
    return isLoopback(hostname) ? 'hostname-is-ip' : 'hostname-is-lan-ip';
  }
  return 'ok';
}

/** True when this browser can create/use passkeys at this address. */
export function isPasskeySupported(): boolean {
  return passkeySupport() === 'ok';
}

/** True when the user dismissed/aborted the native passkey prompt. */
export function isCancellation(err: unknown): boolean {
  return err instanceof DOMException && (err.name === 'NotAllowedError' || err.name === 'AbortError');
}

// --- ceremony deadline -------------------------------------------------------

/**
 * Thrown when a ceremony never settles. Deliberately NOT a DOMException named
 * `AbortError`: that is what a user-cancelled prompt looks like, and the two
 * must be told apart — "you cancelled" and "your device never answered" send
 * someone to look for entirely different problems.
 */
export class PasskeyTimeoutError extends Error {
  constructor(public readonly deadlineMs: number) {
    super(`The passkey ceremony did not answer within ${deadlineMs}ms.`);
    this.name = 'PasskeyTimeoutError';
  }
}

/** True when a ceremony failed because it exceeded our own deadline. */
export function isCeremonyTimeout(err: unknown): boolean {
  return err instanceof PasskeyTimeoutError;
}

/**
 * Our deadline must never fire before the browser's own. The server sends an
 * advisory `timeout` in the ceremony options (py_webauthn's default is 60s), so
 * take that as the floor rather than inventing a number that contradicts it,
 * and add a grace margin: the advisory clock starts inside the browser, while
 * ours starts before the prompt has even been drawn, and a real ceremony is
 * slow on purpose — finding a security key in a drawer, reading the prompt on a
 * phone, waiting out a fingerprint retry. 60s + 30s = 90s for the server's
 * default: long enough that no honest attempt is cut short, short enough that a
 * wedged ceremony doesn't read as "this software is broken".
 */
export const CEREMONY_TIMEOUT_FLOOR_MS = 60_000;
export const CEREMONY_TIMEOUT_GRACE_MS = 30_000;

export function ceremonyDeadlineMs(options: { timeout?: unknown }): number {
  const advertised = options?.timeout;
  const advisory =
    typeof advertised === 'number' && Number.isFinite(advertised) && advertised > 0 ? advertised : 0;
  return Math.max(advisory, CEREMONY_TIMEOUT_FLOOR_MS) + CEREMONY_TIMEOUT_GRACE_MS;
}

/**
 * Bound a credential call. `PublicKeyCredentialCreationOptions.timeout` is only
 * a hint — browsers may ignore it, and when they do the returned promise simply
 * never settles (a flaky external key, a biometric prompt stolen by another
 * app, a device that sleeps mid-prompt). That left the caller pinned on a
 * "creating…" button with no way out.
 *
 * Two mechanisms, because neither alone is enough: the `AbortSignal` is the
 * spec's real cancellation channel and it tears the native prompt down, but a
 * browser that ignored the timeout can equally leave the aborted promise
 * pending — so the race is what actually guarantees the caller gets control
 * back. The signal is best-effort cleanup; the race is the guarantee.
 */
async function withDeadline<T>(
  options: { timeout?: unknown },
  run: (signal: AbortSignal) => Promise<T>,
): Promise<T> {
  const deadline = ceremonyDeadlineMs(options);
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const expiry = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      console.warn(
        `[trug] passkey ceremony got no answer within ${deadline}ms; aborting it so you can retry`,
        { deadline },
      );
      controller.abort(new DOMException('Trug ceremony deadline reached', 'TimeoutError'));
      reject(new PasskeyTimeoutError(deadline));
    }, deadline);
  });

  const running = run(controller.signal);
  // The loser of the race still settles later; keep its rejection from
  // surfacing as an unhandled promise rejection. Race handlers are unaffected.
  running.catch(() => {});
  try {
    return await Promise.race([running, expiry]);
  } finally {
    clearTimeout(timer);
  }
}

// --- ceremony shape translation ---------------------------------------------

type Descriptor = { id: string; type: string; transports?: string[] };

function toDescriptors(list: unknown): PublicKeyCredentialDescriptor[] {
  if (!Array.isArray(list)) return [];
  return (list as Descriptor[]).map((c) => ({
    id: base64urlToBytes(c.id),
    type: c.type as PublicKeyCredentialType,
    transports: c.transports as AuthenticatorTransport[] | undefined,
  }));
}

/** The fields common to both ceremonies' serialised result — id/rawId/type plus
 * the attachment and extension results — encoded as the verify routes expect.
 * Each ceremony spreads this and adds its own `response` (and transports). */
function baseCredentialJson(credential: PublicKeyCredential): Record<string, unknown> {
  return {
    id: credential.id,
    rawId: bytesToBase64url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment ?? undefined,
    clientExtensionResults: credential.getClientExtensionResults(),
  };
}

/**
 * Run the registration ceremony: decode the server options, prompt the
 * authenticator, and serialise the attestation back to the base64url JSON the
 * verify route expects. Rejects if the user cancels or no credential is made.
 */
export async function performRegistration(
  options: Record<string, any>,
): Promise<Record<string, unknown>> {
  const publicKey: PublicKeyCredentialCreationOptions = {
    ...(options as any),
    challenge: base64urlToBytes(options.challenge),
    user: { ...options.user, id: base64urlToBytes(options.user.id) },
    excludeCredentials: toDescriptors(options.excludeCredentials),
  };

  const credential = (await withDeadline(
    options,
    (signal) => navigator.credentials.create({ publicKey, signal }),
  )) as PublicKeyCredential | null;
  if (!credential) throw new Error('No credential was created.');
  const response = credential.response as AuthenticatorAttestationResponse;
  const transports =
    typeof response.getTransports === 'function' ? response.getTransports() : undefined;

  return {
    ...baseCredentialJson(credential),
    // The verify route reads transports at the top level; py_webauthn reads
    // them from the response — carry both.
    transports,
    response: {
      clientDataJSON: bytesToBase64url(response.clientDataJSON),
      attestationObject: bytesToBase64url(response.attestationObject),
      transports,
    },
  };
}

/**
 * Run the sign-in ceremony: decode the server options, prompt the authenticator
 * (discoverable credential — no allowCredentials), and serialise the assertion
 * back to base64url JSON. Rejects if the user cancels or nothing is returned.
 */
export async function performAuthentication(
  options: Record<string, any>,
): Promise<Record<string, unknown>> {
  const publicKey: PublicKeyCredentialRequestOptions = {
    ...(options as any),
    challenge: base64urlToBytes(options.challenge),
    allowCredentials: toDescriptors(options.allowCredentials),
  };

  const credential = (await withDeadline(
    options,
    (signal) => navigator.credentials.get({ publicKey, signal }),
  )) as PublicKeyCredential | null;
  if (!credential) throw new Error('No credential was returned.');
  const response = credential.response as AuthenticatorAssertionResponse;

  return {
    ...baseCredentialJson(credential),
    response: {
      clientDataJSON: bytesToBase64url(response.clientDataJSON),
      authenticatorData: bytesToBase64url(response.authenticatorData),
      signature: bytesToBase64url(response.signature),
      userHandle: response.userHandle ? bytesToBase64url(response.userHandle) : null,
    },
  };
}
