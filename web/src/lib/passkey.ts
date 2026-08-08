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

/** True when this browser can create/use passkeys. */
export function isPasskeySupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.PublicKeyCredential === 'function' &&
    typeof navigator !== 'undefined' &&
    !!navigator.credentials
  );
}

/** True when the user dismissed/aborted the native passkey prompt. */
export function isCancellation(err: unknown): boolean {
  return err instanceof DOMException && (err.name === 'NotAllowedError' || err.name === 'AbortError');
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

  const credential = (await navigator.credentials.create({ publicKey })) as PublicKeyCredential | null;
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

  const credential = (await navigator.credentials.get({ publicKey })) as PublicKeyCredential | null;
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
