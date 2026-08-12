import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  base64urlToBytes,
  bytesToBase64url,
  isPasskeySupported,
  passkeySupport,
  isCancellation,
  isCeremonyTimeout,
  ceremonyDeadlineMs,
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

describe('passkeySupport pre-flight', () => {
  afterEach(() => vi.unstubAllGlobals());

  /** Stub just enough of window/navigator/location to drive one reason. */
  const stub = (opts: {
    secure?: boolean;
    hostname?: string;
    credentialApi?: boolean;
  }) => {
    const { secure = true, hostname = 'trug.example.com', credentialApi = true } = opts;
    vi.stubGlobal('window', {
      isSecureContext: secure,
      PublicKeyCredential: credentialApi ? function () {} : undefined,
      location: { hostname },
    });
    vi.stubGlobal('navigator', { credentials: credentialApi ? {} : undefined });
    vi.stubGlobal('location', { hostname });
  };

  it("says 'ok' on an https host with the credential API present", () => {
    stub({});
    expect(passkeySupport()).toBe('ok');
    expect(isPasskeySupported()).toBe(true);
  });

  it("says 'insecure-context' on a plain-http LAN address", () => {
    // The trial path: http://192.168.1.5:8000. Both insecure AND an IP, but the
    // remedy is the share link, not a rename — so insecure-context must win.
    stub({ secure: false, hostname: '192.168.1.5', credentialApi: false });
    expect(passkeySupport()).toBe('insecure-context');
  });

  it("says 'insecure-context' for a plain-http hostname too", () => {
    stub({ secure: false, hostname: 'raspberrypi.local', credentialApi: false });
    expect(passkeySupport()).toBe('insecure-context');
  });

  it("says 'hostname-is-ip' on loopback by IP, which is secure but unusable", () => {
    // http://127.0.0.1:8000 is a secure context, so the ceremony half-starts and
    // dies on the RP ID. The remedy is one word: open localhost instead.
    stub({ hostname: '127.0.0.1' });
    expect(passkeySupport()).toBe('hostname-is-ip');
  });

  it("says 'hostname-is-ip' for a bracketed IPv6 loopback literal", () => {
    stub({ hostname: '[::1]' });
    expect(passkeySupport()).toBe('hostname-is-ip');
  });

  it("says 'ok' for localhost, the one dotless host that works", () => {
    stub({ hostname: 'localhost' });
    expect(passkeySupport()).toBe('ok');
  });

  it("says 'unsupported' when the address is fine but the browser isn't", () => {
    stub({ hostname: 'trug.example.com', credentialApi: false });
    expect(passkeySupport()).toBe('unsupported');
  });

  it("says 'unsupported' on a secure IP when the browser has no WebAuthn at all", () => {
    // "open localhost instead" changes nothing for a browser that can't make a
    // passkey anywhere, and hides the access-token path that would work.
    stub({ hostname: '127.0.0.1', credentialApi: false });
    expect(passkeySupport()).toBe('unsupported');
  });

  it("still reports the address, not the browser, on an insecure page", () => {
    // The ordering trap: PublicKeyCredential is only exposed in a secure
    // context, so an insecure page has no WebAuthn either. Checking capability
    // first would call every home-network address "unsupported" and drop the
    // only copy that tells anyone what to do.
    stub({ secure: false, hostname: '192.168.1.5', credentialApi: false });
    expect(passkeySupport()).toBe('insecure-context');
  });

  it("distinguishes a LAN IP over https from loopback", () => {
    // https on a LAN address is a secure context, so it reaches the IP check —
    // but "trug is running on this machine" is false on the phone reading it,
    // and localhost there points at the phone.
    stub({ hostname: '192.168.1.42' });
    expect(passkeySupport()).toBe('hostname-is-lan-ip');
  });

  it("keeps loopback-by-IP separate, because that one really is fixed by localhost", () => {
    stub({ hostname: '127.0.0.1' });
    expect(passkeySupport()).toBe('hostname-is-ip');
    stub({ hostname: '[::1]' });
    expect(passkeySupport()).toBe('hostname-is-ip');
  });

  it("says 'unsupported' with no window at all (SSR)", () => {
    vi.stubGlobal('window', undefined);
    expect(passkeySupport()).toBe('unsupported');
    expect(isPasskeySupported()).toBe(false);
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

// A ceremony that never settles used to hang the caller forever: the WebAuthn
// `timeout` option is only a hint, and a flaky security key, a stolen biometric
// prompt or a sleeping device can all leave the promise pending for good.
describe('ceremony deadline', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const regOptions = (extra: Record<string, unknown> = {}) => ({
    challenge: bytesToBase64url(new Uint8Array([1])),
    user: { id: bytesToBase64url(new Uint8Array([2])), name: 'a', displayName: 'a' },
    ...extra,
  });

  it("stays above the server's advisory timeout rather than inventing a shorter one", () => {
    // Firing before the browser's own deadline would abort ceremonies the
    // browser still considers live.
    expect(ceremonyDeadlineMs({ timeout: 60000 })).toBeGreaterThan(60000);
    expect(ceremonyDeadlineMs({ timeout: 120000 })).toBeGreaterThan(120000);
    // No (or nonsense) advisory value still gets a generous floor.
    expect(ceremonyDeadlineMs({})).toBeGreaterThanOrEqual(60000);
    expect(ceremonyDeadlineMs({ timeout: -1 })).toBeGreaterThanOrEqual(60000);
  });

  it('rejects a registration that never settles, and aborts the ceremony', async () => {
    vi.useFakeTimers();
    let signal: AbortSignal | undefined;
    const create = vi.fn((opts: any) => {
      signal = opts.signal;
      return new Promise<never>(() => {});
    });
    vi.stubGlobal('navigator', { credentials: { create } });
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    const deadline = ceremonyDeadlineMs({ timeout: 60000 });
    const pending = performRegistration(regOptions({ timeout: 60000 }));
    let settled = false;
    pending.catch(() => {}).finally(() => (settled = true));

    // Still waiting a whisker before the deadline — an honest slow ceremony
    // (digging a key out of a drawer) must not be cut short.
    await vi.advanceTimersByTimeAsync(deadline - 1000);
    expect(settled).toBe(false);

    await vi.advanceTimersByTimeAsync(2000);
    await expect(pending).rejects.toSatisfy(isCeremonyTimeout);
    expect(signal?.aborted).toBe(true);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('[trug]'), expect.anything());
    warn.mockRestore();
  });

  it('rejects a sign-in that never settles', async () => {
    vi.useFakeTimers();
    const get = vi.fn(() => new Promise<never>(() => {}));
    vi.stubGlobal('navigator', { credentials: { get } });
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    const pending = performAuthentication({ challenge: bytesToBase64url(new Uint8Array([1])) });
    // Attach a handler up front: the rejection lands while the timers are being
    // advanced, before the assertion below could catch it.
    pending.catch(() => {});
    await vi.advanceTimersByTimeAsync(ceremonyDeadlineMs({}) + 1000);
    await expect(pending).rejects.toSatisfy(isCeremonyTimeout);
    warn.mockRestore();
  });

  it('does not report a user cancellation as a timeout', async () => {
    const cancel = new DOMException('The operation was aborted', 'NotAllowedError');
    vi.stubGlobal('navigator', {
      credentials: {
        create: vi.fn(async () => {
          throw cancel;
        }),
      },
    });
    await expect(performRegistration(regOptions())).rejects.toBe(cancel);
    expect(isCeremonyTimeout(cancel)).toBe(false);
  });

  it('leaves no timer running once a ceremony succeeds', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('navigator', {
      credentials: {
        get: vi.fn(async () => ({
          id: 'c',
          rawId: buf(1),
          type: 'public-key',
          getClientExtensionResults: () => ({}),
          response: {
            clientDataJSON: buf(1),
            authenticatorData: buf(2),
            signature: buf(3),
            userHandle: null,
          },
        })),
      },
    });
    await performAuthentication({ challenge: bytesToBase64url(new Uint8Array([1])) });
    expect(vi.getTimerCount()).toBe(0);
  });
});
