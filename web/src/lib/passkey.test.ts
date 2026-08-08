import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  base64urlToBytes,
  bytesToBase64url,
  isPasskeySupported,
  isCancellation,
  performRegistration,
  performAuthentication,
} from './passkey';

const buf = (...bytes: number[]) => new Uint8Array(bytes).buffer;

describe('base64url codec', () => {
  it('round-trips arbitrary byte sequences', () => {
    for (let len = 0; len < 40; len++) {
      const bytes = new Uint8Array(len);
      for (let i = 0; i < len; i++) bytes[i] = (i * 37 + 11) % 256;
      const encoded = bytesToBase64url(bytes);
      // URL-safe alphabet only, and no padding.
      expect(encoded).not.toMatch(/[+/=]/);
      expect([...base64urlToBytes(encoded)]).toEqual([...bytes]);
    }
  });

  it('decodes a known base64url value (with url-safe chars)', () => {
    // 0xFB 0xFF 0xFE -> standard "+/+" ; url-safe "-_-"
    expect(bytesToBase64url(new Uint8Array([0xfb, 0xff, 0xbf]))).toBe('-_-_');
    expect([...base64urlToBytes('-_-_')]).toEqual([0xfb, 0xff, 0xbf]);
  });

  it('accepts an ArrayBuffer as well as a Uint8Array', () => {
    expect(bytesToBase64url(buf(104, 105))).toBe(bytesToBase64url(new Uint8Array([104, 105])));
  });
});

describe('feature detection', () => {
  it('isPasskeySupported reflects window.PublicKeyCredential + navigator.credentials', () => {
    // jsdom provides neither by default.
    expect(isPasskeySupported()).toBe(false);
  });

  it('isCancellation recognises NotAllowedError / AbortError', () => {
    expect(isCancellation(new DOMException('x', 'NotAllowedError'))).toBe(true);
    expect(isCancellation(new DOMException('x', 'AbortError'))).toBe(true);
    expect(isCancellation(new Error('other'))).toBe(false);
  });
});

describe('ceremony translation', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('decodes registration options and serialises the attestation to base64url', async () => {
    let seen: any;
    const create = vi.fn(async ({ publicKey }: any) => {
      seen = publicKey;
      return {
        id: 'cred-id',
        rawId: buf(1, 2, 3),
        type: 'public-key',
        authenticatorAttachment: 'platform',
        getClientExtensionResults: () => ({}),
        response: {
          clientDataJSON: buf(10, 20),
          attestationObject: buf(30, 40),
          getTransports: () => ['internal', 'hybrid'],
        },
      };
    });
    vi.stubGlobal('navigator', { credentials: { create } });

    const options = {
      challenge: bytesToBase64url(new Uint8Array([9, 8, 7])),
      user: { id: bytesToBase64url(new Uint8Array([5, 6])), name: 'alice', displayName: 'alice' },
      rp: { id: 'localhost', name: 'Trug' },
      pubKeyCredParams: [{ type: 'public-key', alg: -7 }],
      excludeCredentials: [{ id: bytesToBase64url(new Uint8Array([1])), type: 'public-key' }],
    };

    const result = await performRegistration(options);

    // Options were decoded to binary before hitting the authenticator.
    expect(seen.challenge).toBeInstanceOf(Uint8Array);
    expect([...seen.challenge]).toEqual([9, 8, 7]);
    expect([...seen.user.id]).toEqual([5, 6]);
    expect([...seen.excludeCredentials[0].id]).toEqual([1]);

    // Attestation serialised back to base64url JSON the verify route expects.
    expect(result.id).toBe('cred-id');
    expect(result.rawId).toBe(bytesToBase64url(new Uint8Array([1, 2, 3])));
    expect(result.transports).toEqual(['internal', 'hybrid']);
    const response = result.response as Record<string, unknown>;
    expect(response.clientDataJSON).toBe(bytesToBase64url(new Uint8Array([10, 20])));
    expect(response.attestationObject).toBe(bytesToBase64url(new Uint8Array([30, 40])));
  });

  it('decodes login options and serialises the assertion to base64url', async () => {
    let seen: any;
    const get = vi.fn(async ({ publicKey }: any) => {
      seen = publicKey;
      return {
        id: 'cred-id',
        rawId: buf(1),
        type: 'public-key',
        authenticatorAttachment: 'platform',
        getClientExtensionResults: () => ({}),
        response: {
          clientDataJSON: buf(11),
          authenticatorData: buf(22),
          signature: buf(33),
          userHandle: buf(44),
        },
      };
    });
    vi.stubGlobal('navigator', { credentials: { get } });

    const result = await performAuthentication({
      challenge: bytesToBase64url(new Uint8Array([7])),
      allowCredentials: [],
    });

    expect([...seen.challenge]).toEqual([7]);
    expect(seen.allowCredentials).toEqual([]);
    const response = result.response as Record<string, unknown>;
    expect(response.clientDataJSON).toBe(bytesToBase64url(new Uint8Array([11])));
    expect(response.authenticatorData).toBe(bytesToBase64url(new Uint8Array([22])));
    expect(response.signature).toBe(bytesToBase64url(new Uint8Array([33])));
    expect(response.userHandle).toBe(bytesToBase64url(new Uint8Array([44])));
  });

  it('throws when the authenticator returns nothing (dismissed prompt)', async () => {
    vi.stubGlobal('navigator', { credentials: { get: vi.fn(async () => null) } });
    await expect(
      performAuthentication({ challenge: bytesToBase64url(new Uint8Array([1])) }),
    ).rejects.toThrow();
  });
});
