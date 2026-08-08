---
name: deploy-trug
description: >-
  Stand up a self-hosted Trug instance for the user's household. Use when the
  user says "deploy trug", "set up my own trug", "self-host this", or otherwise
  wants to get a working Trug running (Docker Compose or Railway) with passkey
  sign-in and their household invited.
---

# Deploy Trug

You are the deploy assistant for **Trug**, a self-hosted shared shopping list.
Your job is to take the user from a fresh clone to a working instance their whole
household can sign into with passkeys. Work through the phases below in order.
Every command here is real and matches this repo — do not invent flags or paths.

Trug is **one container + one SQLite file**. Human sign-in is **passkeys**. The
household is a **dynamic DB roster**: a single one-time **bootstrap token** claims
the very first account, then any enrolled member invites the rest by name from
inside the app. Two machine tokens (ring, MCP) drive the non-human clients. There
are no passwords, no `TRUG_USERS`, and no per-user tokens.

## Phase 0 — Preflight

Confirm you are in a Trug checkout and pick a target.

```sh
ls docker-compose.yml Dockerfile server/trug/app.py
```

Ask the user: **Compose (their own box / Pi) or Railway (managed)?**

- **Compose** needs Docker: `docker --version && docker compose version`.
- **Railway** needs the CLI: `railway --version` (else point them at
  `npm i -g @railway/cli` and `railway login`).

Then collect the one thing you'll reuse everywhere. **Prompt the human** — do not
guess. (There is **no household list to collect** — the roster is built at runtime,
first person via the bootstrap token, everyone else by invite.)

1. **Domain** — the public hostname people will load, or `localhost` for a local
   try. You need it as:
   - `TRUG_RP_ID` = the bare host, no scheme/port (`trug.example.com`).
   - `TRUG_ORIGIN` = the full origin (`https://trug.example.com`).
   These are **required** for passkeys on a real domain and must match the URL the
   browser actually shows, or every passkey ceremony fails. For a local try the
   defaults `localhost` / `http://localhost:8000` already work — WebAuthn treats
   `localhost` as secure.

---

## Phase A — Compose path

### A1. First boot

```sh
docker compose up -d
docker compose logs -f
```

The image builds the PWA + server into one container on port `8000`. The
entrypoint `chown`s the `./data` volume itself and drops to a non-root user via
gosu — **there is no manual `chown` step**; if the user brings one up, tell them
it isn't needed.

Verify health and grab the tokens:

```sh
curl -fsS http://localhost:8000/healthz            # -> {"status":"ok"}
docker compose logs | grep TRUG_TOKEN
```

The banner prints the one-time bootstrap token plus the two machine tokens. It
prints on **every** boot while any token is unpinned (not just the first) — each
unpinned token is regenerated on each restart — and the bootstrap token prints only
while **no account exists yet**. Treat these values as current-boot only until you
pin them.

```sh
docker compose logs | grep -E 'TRUG_BOOTSTRAP_TOKEN|TRUG_TOKEN'
```

| Token | Role |
| --- | --- |
| `TRUG_BOOTSTRAP_TOKEN` | **First-user claim** — one-time, first-user-only. Claims the very first account, then dies. |
| `TRUG_TOKEN_RING` | **Machine** — ring / webhook (`/api/capture`). |
| `TRUG_TOKEN_MCP` | **Machine** — token-based MCP clients. |

Save all of them somewhere for the next steps.

### A2. Configure domain + pin machine tokens

```sh
cp .env.example .env
```

Set (uncomment) in `.env` — there is **no `TRUG_USERS`**:

```
TRUG_RP_ID=<bare host>
TRUG_ORIGIN=<full origin>
```

Pin the machine tokens so this restart doesn't rotate them (paste the banner
values); optionally pin `TRUG_BOOTSTRAP_TOKEN` too if you want a stable value
before the first claim.

```
TRUG_TOKEN_RING=<from banner>
TRUG_TOKEN_MCP=<from banner>
# TRUG_BOOTSTRAP_TOKEN=<from banner>   # optional — else re-read from the logs after restart
```

Apply:

```sh
docker compose up -d
```

(If you didn't pin the bootstrap token, re-read it from
`docker compose logs | grep TRUG_BOOTSTRAP_TOKEN` after this restart before Phase
C, since the pre-restart value is now dead.) Keep the ring / MCP keys pinned so
those clients survive future restarts.

Continue to **Phase C**.

---

## Phase B — Railway path

Use the Railway skill/CLI to drive this; the Trug-specific requirements are:

1. **Deploy from the repo** (`railway up`, or link a GitHub repo in the dashboard).
   Railway builds the `Dockerfile` unchanged.
2. **Add a volume mounted at `/data`** — without it the SQLite DB resets every
   deploy. The container `chown`s the volume on boot, so Railway's root-owned
   mount needs no extra handling.
3. **Set variables**: `TRUG_RP_ID`, `TRUG_ORIGIN` (no `TRUG_USERS`), and
   (recommended) pinned `TRUG_TOKEN_RING` / `TRUG_TOKEN_MCP`, plus any `LLM_*`.
4. **Generate a domain** (Settings → Networking) and make `TRUG_ORIGIN` match it
   exactly (`https://<name>.up.railway.app`) with `TRUG_RP_ID` as the bare host.

Then read the deploy logs for the banner (it prints on every deploy/restart while
any token is unpinned; the bootstrap token prints only while no account exists):

```sh
railway logs | grep -E 'TRUG_BOOTSTRAP_TOKEN|TRUG_TOKEN'
```

Verify health against the public domain (`curl -fsS https://<domain>/healthz`) and
continue to **Phase C**.

---

## Phase C0 — Check the configuration with `trug-doctor`

Both paths converge here. **Before** anyone opens the app to claim an account, run the
built-in doctor and branch on its exit code. This catches the silent killer — a
`TRUG_RP_ID`/`TRUG_ORIGIN` that doesn't match the URL the browser actually loads breaks every
passkey ceremony and the MCP OAuth flow with **no error** in the logs, UI, or healthcheck — so
you catch it here instead of at a ceremony that "just fails".

```sh
docker compose exec trug trug-doctor          # Compose
# railway run trug-doctor                      # Railway (or exec into the service)
```

Exit codes are the contract — branch on them:

- **`0` (healthy):** proceed to Phase C.
- **`1` (warnings):** works but fragile (e.g. an unpinned token, single enrolled member). Read
  each `WARN` line — it says exactly what to do — and decide whether to fix now.
- **`2` (errors):** something is broken (e.g. `http://` on a non-localhost host, an IP as
  `TRUG_RP_ID`, or an origin that doesn't match the observed Host). **Do not tell the user to
  open the app yet** — fix what each `FAIL` line names, restart, and re-run the doctor until it
  is `0`/`1`.

Use `docker compose exec trug trug-doctor --json` for a machine-readable report: each check has
a stable `id` and, where fixable, a `remedy.env` map (e.g. `{"TRUG_ORIGIN": "...", "TRUG_RP_ID":
"..."}`) you can apply directly. The doctor also prints the current tokens (add `--redact`
before sharing output), so it replaces "grep the logs" once an account exists.

## Phase C — Claim the first account

Creating the owner account requires a **secure context**: an HTTPS name or literal
`localhost`. A browser won't create a passkey over `http://<ip>:8000` or `http://<host>.local`,
so claim the owner account over the HTTPS domain (or `localhost` for a local try) **first**,
then invite everyone else from inside the app (Phase D). The token fallback signs in as a
machine principal only — it can't claim a human owner account — so it is not a way around this.

1. Open the instance (`http://localhost:8000` or the public domain).
2. On the sign-in gate, choose **"use an access token"** and paste the
   **`TRUG_BOOTSTRAP_TOKEN`**. Use the current value: if you restarted without
   pinning it, re-read it from the logs first.
3. Because no account exists yet, the gate switches to **"create the first
   account"**: type your name and create a passkey (Face ID / Touch ID / security
   key). You're in as the first member — and the bootstrap token is now **spent**.
   It is one-time and first-user-only; it will never claim another account, and the
   `/auth/bootstrap/claim/*` endpoints 403 from here on.

---

## Phase D — Invite the household (passkeys)

Invites are **session-authed** now — you mint them signed in, from inside the app
(a bootstrap/bearer token can no longer mint invites; that path is gone).

**In the app:** Settings → **Members** → **"+ invite someone"** → type a name (any
new name — no fixed list) → share the generated link. It's single-use and expires
in 24 hours. They open it on their phone, create a passkey, done. Any enrolled
member can invite others or remove members (flat household, no admin tier).

The invite link is `https://your-domain/#invite=<token>` (the `#invite=` fragment
is what the gate reads). Repeat per household member.

Once everyone has a passkey, human sign-in is passkeys-only. **Recovery:** you no longer edit
the SQLite file by hand. Anyone with host access (`docker compose exec`) uses `trug-doctor
recover` — host access *is* the recovery credential, so no extra secret is needed:

```sh
docker compose exec trug trug-doctor recover --invite NAME   # primary escape hatch: mint an
                                                             # invite link, preserves all data
docker compose exec trug trug-doctor recover --reset-bootstrap  # full lockout: re-open the
                                                             # one-time first-user claim
docker compose exec trug trug-doctor recover --revoke-sessions  # stolen device: kill sessions
```

`--invite` needs no session and works even when nobody can sign in — prefer it whenever any
member can still be reached. `--reset-bootstrap` is for a **total** lockout (every device
lost): it non-destructively re-opens the first-user claim (no rows deleted) and refuses while an
enrolled credential still exists unless you pass `--force`. Bare `recover` lists the options and
changes nothing.

---

## Phase E — Connect AI assistants (MCP) & the ring

**MCP for Claude (claude.ai / Desktop / Claude Code):** add a **custom connector**
with just the URL:

```
https://your-domain/mcp
```

Trug is its own OAuth server — the user signs in with their passkey and approves a
consent screen. No client id, secret, or token to paste.

**Token-based / Pebble ring** (can't do interactive OAuth): use the URL plus a
bearer. **Settings → Connections** (reveal + copy) shows the live values, but that
screen requires a **passkey/cookie session** — a bearer/bootstrap token gets a 401
there. If you're still on the token fallback (no passkey enrolled yet), read the
machine tokens from your `.env`/Railway vars or the boot banner instead:

- Ring **webhook**: `POST https://your-domain/api/capture`, `multipart/form-data`
  with a `transcription` field, `Authorization: Bearer <ring token>`
  (`TRUG_TOKEN_RING`).
- Ring **MCP** route: `https://your-domain/mcp`, Streamable HTTP,
  `Authorization: Bearer <MCP token>` (`TRUG_TOKEN_MCP`).

Quick webhook smoke test:

```sh
curl -fsS -X POST https://your-domain/api/capture \
  -H "Authorization: Bearer $TRUG_TOKEN_RING" \
  -H "Content-Type: application/json" \
  -d '{"text":"milk, eggs and bread"}'
```

## Phase F — Optional: LLM enrichment

Trug is fully iconed/aisled for common groceries with **no key** (built-in tier-0
icon map). A key adds prose-untangling and rare-item coverage. Two ways to configure
it, and the in-app one wins:

- **Env:** set in `.env` (or Railway vars) `LLM_API_KEY`, optionally `LLM_MODEL`
  (default `claude-haiku-4-5`), `LLM_BASE_URL`, then restart to apply. This is the
  startup default only.
- **In-app (Settings → AI enrichment):** a signed-in human picks a provider, pastes a
  key, tests, and saves — no restart. A saved in-app config **silently overrides** the
  `LLM_*` env vars and takes effect immediately, so "set env + restart" isn't the only
  path and isn't authoritative if someone has saved a config in the UI. **Clear** in the
  UI reverts to the env values.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Passkeys fail on a LAN address (`http://<ip>` / `.local`) | HTTPS (or literal `localhost`) is a prerequisite for creating accounts — a browser won't make a passkey in an insecure context and rp_id can't be an IP | Give the box a trusted HTTPS name: `tailscale serve --bg 8000` (one command → `https://<name>.ts.net`) or the compose file's `cloudflared` block, then set `TRUG_RP_ID`/`TRUG_ORIGIN` to that name. The token fallback is **machine / emergency access only** — it signs in as a machine principal and cannot claim a human owner account, so it's not a way to skip HTTPS. |
| Passkey create/sign-in always fails | `TRUG_RP_ID` / `TRUG_ORIGIN` don't match the URL in the browser | Set `TRUG_RP_ID` to the bare host and `TRUG_ORIGIN` to the exact origin (scheme + host, and port if non-standard); restart. On Railway they must equal the generated domain. |
| Container can't write the DB / permission errors on `/data` | Rare — the entrypoint chowns the volume on boot | Confirm the container runs the bundled `docker-entrypoint.sh` (don't override the entrypoint) and that `./data` isn't mounted read-only. No manual `chown` should be needed. |
| Ring / MCP client stopped working after a restart | Generated tokens rotate on restart until pinned | Pin `TRUG_TOKEN_RING` / `TRUG_TOKEN_MCP` in `.env` (or Railway vars) to the values from the first-boot banner. |
| Invite link 400 "invalid or expired" | Single-use, 24h TTL; already used or old | Mint a fresh one (Settings → Members → "+ invite someone"). |
| Bootstrap token gets 403 / can't create the first account | The instance is already claimed (any user exists) — bootstrap is one-time, first-user-only | Sign in with your passkey, or have an enrolled member send you an invite link. Full lockout (all devices lost): `docker compose exec trug trug-doctor recover --reset-bootstrap` re-opens the claim non-destructively (no DB surgery). |
| Old UI after deploying an update / stale assets | PWA service-worker cache | Hard-reload, or remove the installed PWA and reinstall; on mobile, close all tabs first. |
| `/mcp` returns 401 in a connector | Expected before OAuth — it advertises the auth flow | Complete the connector sign-in (passkey + consent), or for token clients send `Authorization: Bearer <MCP token>`. |
| No token banner in logs | Every token is already pinned (env/`.env`), so nothing is generated to print | Read them from your `.env` / Railway vars. Settings → Connections also shows the machine tokens, but only once you're on a passkey session (it 401s on a bootstrap/bearer token). |

## Done

Report to the user: the URL, that their household has been invited (list who), and
the MCP connector URL. Remind them to keep the pinned tokens private.
