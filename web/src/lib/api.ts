import type { Item, ListResponse, CatalogEntry, ItemStatus } from './types';

const TOKEN_KEY = 'trug_token';

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export function setToken(t: string): void {
  localStorage.setItem(TOKEN_KEY, t);
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

/** Forget a stored bearer (e.g. a stale one that failed validation on boot). */
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

/**
 * Single shared credential probe: a plain GET /api/list, returning whether the
 * server accepted it. `headers` carries a candidate bearer when validating one;
 * omitted, the same-origin session cookie is the only credential. Network
 * failures resolve to false — a probe should never wave auth through on a fluke.
 */
async function probeList(headers?: Record<string, string>): Promise<boolean> {
  try {
    const res = await fetch('/api/list', { headers, credentials: 'same-origin' });
    return res.ok;
  } catch {
    return false;
  }
}

/**
 * Checks a candidate token against the server before it's trusted, independent
 * of whatever token (if any) is already stored.
 */
export function validateToken(token: string): Promise<boolean> {
  return probeList({ Authorization: `Bearer ${token}` });
}

/**
 * Probes whether an HttpOnly session cookie is already good for the API (no
 * bearer, cookies riding along same-origin). A human is "signed in" when EITHER
 * a bearer token is stored (legacy/machine) OR this probe passes.
 */
export function hasCookieSession(): Promise<boolean> {
  return probeList();
}

/**
 * Tri-state auth probe for the live stream: distinguishes a CONFIRMED auth loss
 * (a 401/403 from the server) from an inconclusive network error, so a transient
 * outage never mistakenly signs the user out. Uses the stored bearer if present,
 * else the session cookie.
 */
export type AuthProbe = 'ok' | 'lost' | 'error';

/**
 * Shared tri-state probe against /api/list. `ok` = accepted, `lost` = a
 * CONFIRMED 401/403, `error` = an inconclusive network failure (or any other
 * status). `headers` carries a candidate bearer when one is being checked;
 * omitted, the same-origin session cookie is the credential.
 */
async function probeStatus(headers?: Record<string, string>): Promise<AuthProbe> {
  try {
    const res = await fetch('/api/list', { headers, credentials: 'same-origin' });
    if (res.ok) return 'ok';
    if (res.status === 401 || res.status === 403) return 'lost';
    return 'error';
  } catch {
    return 'error';
  }
}

export function probeAuthStatus(): Promise<AuthProbe> {
  const token = getToken();
  return probeStatus(token ? { Authorization: `Bearer ${token}` } : undefined);
}

/**
 * Tri-state check of a CANDIDATE pasted token, independent of whatever token
 * (if any) is already stored. Unlike {@link validateToken} (which collapses a
 * network error to `false` and would lie "invalid"), this distinguishes a
 * CONFIRMED-invalid token (`lost`) from an unreachable server (`error`) — so the
 * gate only diverts a genuinely-rejected token to the first-account claim flow.
 */
export function probeToken(token: string): Promise<AuthProbe> {
  return probeStatus({ Authorization: `Bearer ${token}` });
}

const URL_TOKEN_ERROR_KEY = 'trug_url_token_error';

/**
 * Flags (in sessionStorage, so it survives the `mount(App)` boundary between
 * main.ts and AuthGate without prop-drilling) that the ?token= the app was
 * opened with failed validation, so the gate can surface it once.
 */
export function markUrlTokenInvalid(): void {
  sessionStorage.setItem(URL_TOKEN_ERROR_KEY, '1');
}

/** Reads and clears the flag set by {@link markUrlTokenInvalid}. */
export function consumeUrlTokenError(): boolean {
  const flagged = sessionStorage.getItem(URL_TOKEN_ERROR_KEY) === '1';
  if (flagged) sessionStorage.removeItem(URL_TOKEN_ERROR_KEY);
  return flagged;
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const token = getToken();
  const headers: Record<string, string> = {
    ...((init.headers as Record<string, string>) ?? {}),
  };
  // Legacy/machine bearer when stored; the session cookie rides along via
  // `credentials: 'same-origin'` for humans (and is ignored when a bearer wins).
  if (token) headers.Authorization = `Bearer ${token}`;
  if (init.body != null) headers['Content-Type'] = 'application/json';

  const res = await fetch(path, { ...init, headers, credentials: 'same-origin' });
  if (!res.ok) {
    throw new ApiError(res.status, await errorMessage(res, `${init.method ?? 'GET'} ${path}`));
  }
  return res;
}

/** Prefer the server's `{detail}` for a readable message, else a terse fallback. */
async function errorMessage(res: Response, context: string): Promise<string> {
  try {
    const body = await res.clone().json();
    if (body && typeof body.detail === 'string') return body.detail;
  } catch {
    /* non-JSON body */
  }
  return `${context} failed: ${res.status}`;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await request(path, init);
  return (await res.json()) as T;
}

export const api = {
  list(): Promise<ListResponse> {
    return requestJson<ListResponse>('/api/list');
  },

  async addItem(item: { id: string; name: string; note?: string }): Promise<{ item: Item; created: boolean }> {
    const res = await request('/api/items', {
      method: 'POST',
      body: JSON.stringify(item),
    });
    const created = res.headers.get('X-Created') !== 'false';
    return { item: (await res.json()) as Item, created };
  },

  setStatus(id: string, status: ItemStatus): Promise<Item> {
    return requestJson<Item>(`/api/items/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    });
  },

  update(
    id: string,
    fields: Partial<Pick<Item, 'status' | 'note' | 'category' | 'sort_key'>>,
  ): Promise<Item> {
    return requestJson<Item>(`/api/items/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(fields),
    });
  },

  async remove(id: string): Promise<void> {
    await request(`/api/items/${id}`, { method: 'DELETE' });
  },

  async clearChecked(): Promise<number> {
    const { cleared } = await requestJson<{ cleared: number }>('/api/list/clear-checked', {
      method: 'POST',
    });
    return cleared;
  },

  search(q: string): Promise<CatalogEntry[]> {
    return requestJson<CatalogEntry[]>(`/api/catalog?${new URLSearchParams({ q })}`);
  },

  top(n: number): Promise<CatalogEntry[]> {
    return requestJson<CatalogEntry[]>(`/api/catalog/top?n=${n}`);
  },

  // --- passkey / session auth ---------------------------------------------
  // Ceremony options/results are opaque WebAuthn JSON; the codec + navigator
  // glue lives in lib/passkey.ts. Every call rides the session cookie via
  // `request()` (credentials: 'same-origin').
  auth: {
    /**
     * Is this instance still claimable — zero users, or a live recovery reopen
     * (`trug-doctor recover --reset-bootstrap`)? Unauthenticated probe used by
     * the gate both to show the first-run onboarding and to route a pasted
     * token into the first-account bootstrap flow.
     */
    bootstrapState(): Promise<BootstrapState> {
      return requestJson<BootstrapState>('/auth/bootstrap/state');
    },

    /**
     * First-account claim — registration options. The bootstrap token is sent as
     * a one-shot bearer (never stored); the server 403s unless the roster is
     * empty and the token matches.
     */
    bootstrapClaimOptions(
      name: string,
      token: string,
    ): Promise<PublicKeyCredentialCreationOptionsJSON> {
      return requestJson('/auth/bootstrap/claim/options', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: JSON.stringify({ name }),
      });
    },

    /** First-account claim — verify the attestation, create user #1, open a session. */
    bootstrapClaimVerify(name: string, credential: unknown, token: string): Promise<AuthResult> {
      return requestJson<AuthResult>('/auth/bootstrap/claim/verify', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: JSON.stringify({ name, credential }),
      });
    },

    /** The household roster + the caller's own name (to mark "you"). */
    listMembers(): Promise<MemberRoster> {
      return requestJson<MemberRoster>('/auth/users');
    },

    /** Invite a new member by name — creates the pending user and mints a link. */
    inviteUser(name: string): Promise<InviteResponse> {
      return requestJson<InviteResponse>('/auth/invite', {
        method: 'POST',
        body: JSON.stringify({ name }),
      });
    },

    /** Remove a member (cascades). Removing yourself ends your session server-side. */
    async removeMember(name: string): Promise<void> {
      await request(`/auth/members/${encodeURIComponent(name)}`, { method: 'DELETE' });
    },

    /** Registration ceremony: server challenge for a valid invite. */
    registerOptions(invite: string): Promise<PublicKeyCredentialCreationOptionsJSON> {
      return requestJson('/auth/register/options', {
        method: 'POST',
        body: JSON.stringify({ invite }),
      });
    },

    /** Registration ceremony: verify the attestation and open a session. */
    registerVerify(invite: string, credential: unknown): Promise<AuthResult> {
      return requestJson<AuthResult>('/auth/register/verify', {
        method: 'POST',
        body: JSON.stringify({ invite, credential }),
      });
    },

    /** Sign-in ceremony: server challenge (discoverable credential — no user). */
    loginOptions(): Promise<PublicKeyCredentialRequestOptionsJSON> {
      return requestJson('/auth/login/options', { method: 'POST' });
    },

    /** Sign-in ceremony: verify the assertion and open a session. */
    loginVerify(credential: unknown): Promise<AuthResult> {
      return requestJson<AuthResult>('/auth/login/verify', {
        method: 'POST',
        body: JSON.stringify({ credential }),
      });
    },

    /** End the current session (revoke server-side + clear the cookie). */
    async logout(): Promise<void> {
      await request('/auth/logout', { method: 'POST' });
    },

    /** List this user's active sessions (current device flagged). */
    sessions(): Promise<SessionInfo[]> {
      return requestJson<{ sessions: SessionInfo[] }>('/auth/sessions').then((r) => r.sessions);
    },

    /** Revoke one session by id. */
    async revokeSession(id: string): Promise<void> {
      await request(`/auth/sessions/${id}`, { method: 'DELETE' });
    },

    /** Self-serve setup values (MCP + ring). Session-only on the server. */
    connections(): Promise<Connections> {
      return requestJson<Connections>('/auth/connections');
    },

    /** Current BYOK LLM config (masked hint only — never the full key). */
    getLlmConfig(): Promise<LlmConfig> {
      return requestJson<LlmConfig>('/auth/llm-config');
    },

    /** Persist the LLM config; the server reloads enrichment in place. */
    putLlmConfig(cfg: LlmConfigInput): Promise<LlmConfig> {
      return requestJson<LlmConfig>('/auth/llm-config', {
        method: 'PUT',
        body: JSON.stringify(cfg),
      });
    },

    /** Live hello-world test: enrich "milk" against the candidate config. */
    testLlmConfig(cfg: LlmConfigInput): Promise<LlmTestResult> {
      return requestJson<LlmTestResult>('/auth/llm-config/test', {
        method: 'POST',
        body: JSON.stringify(cfg),
      });
    },

    /** Clear the stored config; enrichment reverts to the env config / none. */
    clearLlmConfig(): Promise<LlmConfig> {
      return requestJson<LlmConfig>('/auth/llm-config', { method: 'DELETE' });
    },

    /**
     * Fetch the provider's live text-generation model ids for the model picker.
     * Server filters to chat models and reuses the stored key when the body omits
     * one. On a provider error it returns `{models: [], detail}` — never throws
     * for a provider fault, only for a transport/HTTP error.
     */
    listLlmModels(cfg: LlmConfigInput): Promise<LlmModels> {
      return requestJson<LlmModels>('/auth/llm-config/models', {
        method: 'POST',
        body: JSON.stringify(cfg),
      });
    },
  },
};

/** BYOK provider ids, mirroring the server enum. */
export type LlmProvider = 'anthropic' | 'gemini' | 'openai' | 'ollama' | 'custom';

/** The GET/PUT shape — a masked key hint, never the full key. */
export interface LlmConfig {
  provider: LlmProvider | null;
  model: string | null;
  base_url: string | null;
  configured: boolean;
  key_hint: string | null;
  source: 'settings' | 'env' | 'none';
  /** True when a stored key exists but can no longer be decrypted (rotated/removed
   *  TRUG_SECRET or corrupt ciphertext) — the UI prompts a re-entry. */
  unreadable_key?: boolean;
}

export interface LlmConfigInput {
  provider: LlmProvider;
  api_key?: string;
  model?: string;
  base_url?: string;
}

/** Live model listing: the provider's text-generation ids, or an error detail.
 *  ``labels`` maps id → human-friendly display name where the provider supplied
 *  one (e.g. Anthropic). The listing is often incomplete — a working model may
 *  be absent — so the UI always allows typing an id in. */
export interface LlmModels {
  models: string[];
  labels?: Record<string, string>;
  detail?: string;
}

export interface LlmTestResult {
  ok: boolean;
  detail: string;
  result?: { icon: string | null; category: string };
}

export interface Connections {
  mcp_url: string;
  mcp_token: string;
  webhook_url: string;
  ring_token: string;
}

export interface InviteResponse {
  invite: string;
  name: string;
}

/** Whether this instance is still claimable (zero users → bootstrap open). */
export interface BootstrapState {
  claimable: boolean;
}

/** A household member row for the Members UI. */
export interface Member {
  name: string;
  enrolled: boolean;
  created_at: string;
  credential_count: number;
}

/** The roster plus the caller's own name, so the UI can mark "you". */
export interface MemberRoster {
  users: Member[];
  me: string;
}

export interface AuthResult {
  ok: boolean;
  user: string;
}

export interface SessionInfo {
  id: string;
  created_at: string;
  last_seen: string;
  user_agent: string | null;
  current: boolean;
}

/** Opaque WebAuthn ceremony JSON — passed straight to lib/passkey.ts. */
export type PublicKeyCredentialCreationOptionsJSON = Record<string, unknown>;
export type PublicKeyCredentialRequestOptionsJSON = Record<string, unknown>;
