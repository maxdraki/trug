import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { api, setToken, getToken, ApiError, probeAuthStatus, validateToken, REQUEST_TIMEOUT_MS } from './api';

function jsonResponse(body: unknown, init: { status?: number; headers?: Record<string, string> } = {}) {
  const status = init.status ?? 200;
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
  });
}

describe('api client', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    setToken('tok-123');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('setToken/getToken round-trip via localStorage', () => {
    setToken('abc');
    expect(getToken()).toBe('abc');
    expect(localStorage.getItem('trug_token')).toBe('abc');
  });

  it('attaches the bearer Authorization header', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ active: {}, checked: [] }));
    await api.list();
    const [, init] = fetchMock.mock.calls[0];
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok-123');
  });

  it('list hits GET /api/list and returns the payload', async () => {
    const payload = { active: { produce: [] }, checked: [] };
    fetchMock.mockResolvedValue(jsonResponse(payload));
    const res = await api.list();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/list');
    expect(init.method ?? 'GET').toBe('GET');
    expect(res).toEqual(payload);
  });

  it('addItem derives created=true from X-Created header', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ id: 'x' }, { headers: { 'X-Created': 'true' } }),
    );
    const { item, created } = await api.addItem({ id: 'x', name: 'Milk' });
    expect(created).toBe(true);
    expect(item).toEqual({ id: 'x' });
  });

  it('addItem derives created=false when X-Created is "false" (dedup hit)', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ id: 'x' }, { headers: { 'X-Created': 'false' } }),
    );
    const { created } = await api.addItem({ id: 'x', name: 'Milk' });
    expect(created).toBe(false);
  });

  it('setStatus PATCHes the item status', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: 'x', status: 'checked' }));
    const item = await api.setStatus('x', 'checked');
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/items/x');
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body as string)).toEqual({ status: 'checked' });
    expect(item.status).toBe('checked');
  });

  it('update PATCHes arbitrary fields', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: 'x', note: 'hi' }));
    await api.update('x', { note: 'hi', category: 'dairy' });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/items/x');
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body as string)).toEqual({ note: 'hi', category: 'dairy' });
  });

  it('remove DELETEs and resolves void on 204', async () => {
    fetchMock.mockResolvedValue(jsonResponse(null, { status: 204 }));
    await expect(api.remove('x')).resolves.toBeUndefined();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/items/x');
    expect(init.method).toBe('DELETE');
  });

  it('clearChecked returns the cleared count n', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ cleared: 7 }));
    const n = await api.clearChecked();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/list/clear-checked');
    expect(init.method).toBe('POST');
    expect(n).toBe(7);
  });

  it('search hits the catalog endpoint with the query', async () => {
    fetchMock.mockResolvedValue(jsonResponse([{ name_norm: 'milk' }]));
    const res = await api.search('mi lk');
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/catalog?q=mi+lk');
    expect(res).toHaveLength(1);
  });

  it('top hits the catalog/top endpoint with n', async () => {
    fetchMock.mockResolvedValue(jsonResponse([]));
    await api.top(24);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/catalog/top?n=24');
  });

  it('forget DELETEs the catalog endpoint with the key as a query parameter', async () => {
    fetchMock.mockResolvedValue(jsonResponse(null, { status: 204 }));
    await api.forget('marty rice');
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/catalog?name_norm=marty+rice');
    expect(init.method).toBe('DELETE');
  });

  it('forget rides out the unload only when the caller asks it to', async () => {
    // The tray defers a forget for five seconds, so pocketing the phone inside
    // that window means the request is made as the document is going away:
    // without `keepalive` the browser cancels it along with the page and the
    // shortcut survives, having been reported as forgotten. Everywhere else the
    // option is left off — keepalive requests are capped and deprioritised, and
    // an ordinary in-page forget wants neither.
    fetchMock.mockResolvedValue(jsonResponse(null, { status: 204 }));
    await api.forget('marty rice', { keepalive: true });
    expect(fetchMock.mock.calls[0][1].keepalive).toBe(true);

    await api.forget('papa dums');
    expect(fetchMock.mock.calls[1][1].keepalive).toBeFalsy();
  });

  it('forget encodes every punctuation mark a catalogue key can hold', async () => {
    // A key is never a path segment (a "/" in one is unroutable), so the only
    // thing standing between "salt / pepper" and a permanent "Couldn't forget"
    // is that these come back out of the query string byte-for-byte.
    const cases: [string, string][] = [
      ['salt / pepper', 'salt+%2F+pepper'], // slash — the defect
      ['100% juice', '100%25+juice'], // percent — a bare % is an invalid escape
      ['salt & vinegar', 'salt+%26+vinegar'], // ampersand — parameter separator
      ["za'atar", 'za%27atar'], // apostrophe
      ['7 + 7 bars', '7+%2B+7+bars'], // plus — would decode back to a space
      ['item #4', 'item+%234'], // hash — a fragment delimiter
      ['what? sauce', 'what%3F+sauce'], // question mark — starts the query
      ['crème fraîche', 'cr%C3%A8me+fra%C3%AEche'], // non-ASCII, UTF-8 encoded
    ];
    for (const [key, encoded] of cases) {
      fetchMock.mockResolvedValue(jsonResponse(null, { status: 204 }));
      await api.forget(key);
      const [url] = fetchMock.mock.calls.at(-1)!;
      expect(url).toBe(`/api/catalog?name_norm=${encoded}`);
      // And the encoding is reversible: what the server parses is the key.
      expect(new URLSearchParams(new URL(url, 'http://x').search).get('name_norm')).toBe(key);
    }
  });

  it('listLlmModels POSTs the config and returns the model list', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ models: ['gemini-3.6-flash', 'gemini-2.5-flash'] }),
    );
    const res = await api.auth.listLlmModels({ provider: 'gemini', api_key: 'g-key' });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/auth/llm-config/models');
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('same-origin');
    expect(JSON.parse(init.body as string)).toEqual({ provider: 'gemini', api_key: 'g-key' });
    expect(res.models).toEqual(['gemini-3.6-flash', 'gemini-2.5-flash']);
  });

  it('listLlmModels surfaces a provider error detail with an empty list', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ models: [], detail: '401 unauthorized' }));
    const res = await api.auth.listLlmModels({ provider: 'openai', api_key: 'sk-bad' });
    expect(res.models).toEqual([]);
    expect(res.detail).toBe('401 unauthorized');
  });

  it('bootstrapClaimOptions sends the bootstrap token as a one-shot bearer', async () => {
    localStorage.clear(); // no stored token on a fresh instance
    fetchMock.mockResolvedValue(jsonResponse({ challenge: 'c' }));
    await api.auth.bootstrapClaimOptions('Alice', 'boot-secret');
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/auth/bootstrap/claim/options');
    expect(init.method).toBe('POST');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer boot-secret');
    expect(JSON.parse(init.body as string)).toEqual({ name: 'Alice' });
  });

  it('inviteUser POSTs the name and removeMember DELETEs by name', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ invite: 'tok', name: 'guest' }));
    await api.auth.inviteUser('guest');
    let [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/auth/invite');
    expect(JSON.parse(init.body as string)).toEqual({ name: 'guest' });

    fetchMock.mockResolvedValue(jsonResponse(null, { status: 204 }));
    await api.auth.removeMember('a b');
    [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe('/auth/members/a%20b');
    expect(init.method).toBe('DELETE');
  });

  describe('hung requests', () => {
    /** A fetch that never settles on its own — only the abort signal ends it. */
    function hangingFetch() {
      return vi.fn(
        (_url: string, init: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init.signal!.addEventListener('abort', () => reject(init.signal!.reason));
          }),
      );
    }

    it('aborts a request that never settles instead of waiting for ever', async () => {
      // A restarted backend behind a proxy that holds the socket, a captive
      // portal, iOS freezing an installed PWA: the promise never settles, so
      // the op queue parks on it and every later tap is handed the same dead
      // promise. A rejecting fetch self-heals; only a hang is fatal.
      vi.useFakeTimers();
      try {
        fetchMock.mockImplementation(hangingFetch());
        const settled = api.list().then(() => 'resolved' as const, (e) => e);
        await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS + 1);
        const err = await settled;
        expect(err).toBeInstanceOf(DOMException);
        expect((err as DOMException).name).toBe('TimeoutError');
        // Not an ApiError, so the store routes it down the offline path.
        expect(err).not.toBeInstanceOf(ApiError);
      } finally {
        vi.useRealTimers();
      }
    });

    it('clears the timeout once a request settles normally', async () => {
      vi.useFakeTimers();
      try {
        fetchMock.mockResolvedValue(jsonResponse({ active: {}, checked: [] }));
        await api.list();
        expect(vi.getTimerCount()).toBe(0);
      } finally {
        vi.useRealTimers();
      }
    });

    it('a hung auth probe resolves rather than hanging the gate', async () => {
      vi.useFakeTimers();
      try {
        fetchMock.mockImplementation(hangingFetch());
        const status = probeAuthStatus();
        const valid = validateToken('candidate');
        await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS + 1);
        expect(await status).toBe('error');
        expect(await valid).toBe(false);
      } finally {
        vi.useRealTimers();
      }
    });
  });

  it('surfaces a non-2xx response as ApiError with .status', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: 'nope' }, { status: 404 }));
    const err = await api.list().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });
});
