# Configuration

Every setting is optional. Trug boots with no configuration at all, generates what it needs, and
tells you what it generated. You only start setting things when you move off `localhost`.

Settings come from three places, and the first one that has a value wins:

1. **Environment variables** — via `.env` with Docker Compose, or your platform's variables tab.
2. **`config.yaml`** in the working directory, if you'd rather keep everything in one file.
3. **Built-in defaults.**

One sharp edge in that order: an environment variable set to an *empty* string still counts as
set, and beats `config.yaml`. If you're using both, comment the variable out rather than blanking
it.

If you installed with the one-liner, the `.env` is `~/.trug/.env` — that's the file everything
below lives in. Edit it by hand, then `trug up` to apply the changes. Re-running the installer
preserves whatever it finds there, so hand edits survive.

## The settings

### Domain and passkeys

| Variable | Default | Notes |
| --- | --- | --- |
| `TRUG_RP_ID` | `localhost` | The bare host passkeys are bound to. No scheme, no port, never an IP. |
| `TRUG_ORIGIN` | `http://localhost:8000` | The full origin people load, exactly as the browser shows it. |
| `TRUG_SESSION_DAYS` | `60` | How long a passkey session lasts. |

Required together once you serve Trug on a real name, and they must match the address in the
browser or every passkey ceremony fails silently.

Set these two with `trug set-origin https://your-name` rather than by hand. It derives the RP ID
from the origin, restarts, and runs the doctor on the result — and it refuses the four inputs that
look fine and break passkeys anyway: a bare hostname, a plain-`http` origin, a URL with a path,
and an IP address. Editing them yourself works too; see
[remote access](remote-access.md#tell-trug-its-new-name), and run the doctor afterwards.

### Tokens

| Variable | Default | Notes |
| --- | --- | --- |
| `TRUG_BOOTSTRAP_TOKEN` | generated | Claims the very first account. One-time, first-user-only. Printed on boot while no account exists. |
| `TRUG_TOKEN_RING` | generated | Machine key for the ring webhook (`/api/capture`). |
| `TRUG_TOKEN_MCP` | generated | Machine key for token-based MCP and automation clients. |

**Anything you don't pin is regenerated on every restart** and reprinted in the boot banner. Pin
the two machine keys before the ring or an MCP client depends on them, or a restart logs them out.

There is no household roster here. `TRUG_USERS` is retired — if it's still set it's ignored rather
than an error. The roster lives in the database: first person claims, everyone else is invited.

### Storage

| Variable | Default | Notes |
| --- | --- | --- |
| `TRUG_DB_PATH` | `/data/trug.db` | The SQLite file. In the image this is the mounted volume. |
| `TRUG_STATIC_DIR` | `/app/static` | The built PWA. |
| `PORT` | `8000` | The container binds this and the compose file publishes it. Must be an integer. |

### Behind a proxy

| Variable | Default | Notes |
| --- | --- | --- |
| `TRUG_TRUSTED_PROXY_HOPS` | `0` | How many reverse proxies sit in front of Trug. |

`0` means directly exposed: `X-Forwarded-For` is ignored entirely and the socket peer is used, so
a client can't forge a header to dodge the rate limiter. `1` means one proxy — Cloudflare, nginx,
Caddy, Tailscale — and the address *your* proxy inserted is used rather than the client-supplied
leftmost value. Use `N` for `N` chained trusted proxies.

Because the default is `0`, **a proxied deployment must set this**, or every request looks like it
came from the proxy and the whole household shares one rate-limit bucket.

While you're there, bind Trug to loopback so nobody can reach it around the proxy — the repo's
`docker-compose.yml` carries a commented line for it. A client that can hit the container directly
can forge `X-Forwarded-For` freely.

The installer's `~/.trug/docker-compose.yml` has no such line: put `127.0.0.1:` on the front of
its `ports:` entry yourself, and expect to do it again after any installer re-run, which rewrites
that file.

### Rate limiting

| Variable | Default | Notes |
| --- | --- | --- |
| `TRUG_RATE_LIMIT_ENABLED` | `true` | Turn off if your proxy already limits. |

Trug ships a basic in-process limiter on the sensitive auth endpoints — bootstrap claim, passkey
login and register, invite redemption, and `/oauth/token`. It's enough to blunt brute force on a
directly-exposed box. It is not a replacement for a real edge limiter: put Trug behind a reverse
proxy for production-grade limiting, and keep the proxy there even with Trug's limiter on.

### LLM enrichment

All optional — Trug icons and sorts common groceries with no key at all. See
[integrations](integrations.md#bring-your-own-key) for what a key actually buys you.

| Variable | Default | Notes |
| --- | --- | --- |
| `LLM_API_KEY` | — | Enables enrichment when present. |
| `LLM_MODEL` | `claude-haiku-4-5` | Model id. |
| `LLM_BASE_URL` | — | Point at an OpenAI-compatible endpoint. This switches the wire protocol, not just the host. |
| `TRUG_SECRET` | — | Encrypts an in-app saved key at rest. |

`LLM_BASE_URL` is the one that surprises people: any non-empty value makes Trug POST OpenAI-shaped
bodies to `{base_url}/chat/completions` with `Authorization: Bearer`, instead of Anthropic's
`x-api-key` and Messages API. The endpoint has to speak the OpenAI protocol — an Anthropic-format
proxy will fail. For Gemini that's
`https://generativelanguage.googleapis.com/v1beta/openai`.

`TRUG_SECRET` matters if a human saves a key in **Settings → AI enrichment**. With it set, that
key is encrypted (Fernet) in the SQLite file; without it, it's stored in plaintext. Fine for a
single-household box, worth setting if the database gets backed up somewhere else. Keep it stable
— rotating it makes existing saved keys unreadable. It has no effect on `LLM_API_KEY`, which lives
in the environment anyway.

These are the *startup default*. A config saved in Settings overrides them immediately, with no
restart, and clearing it in the UI reverts to whatever is here.

### Tunnels

| Variable | Default | Notes |
| --- | --- | --- |
| `CLOUDFLARE_TUNNEL_TOKEN` | — | Only used by the commented `cloudflared` service in the repo's `docker-compose.yml`. |

## Using `config.yaml` instead

Every setting above can come from a `config.yaml` in the working directory, which is a tidier home
for a long list than a `.env`:

```yaml
rp_id: trug.example.com
origin: https://trug.example.com
tokens:
  ring: …
  mcp: …
bootstrap_token: …
db_path: /data/trug.db
walk_order: [Produce, Bakery, Dairy & Eggs, …]
household: …
llm_api_key: …
llm_model: claude-haiku-4-5
session_days: 60
rate_limit_enabled: true
trusted_proxy_hops: 1
```

Trug loads it on start-up from the process working directory, which inside the image is `/app` —
so mount it there:

```yaml
volumes:
  - ./config.yaml:/app/config.yaml:ro
```

Environment variables still take precedence over anything in the file.

## Checking what's actually live

```sh
trug status                             # if you used the installer — from anywhere
docker compose exec trug trug-doctor    # if you didn't — from inside the clone
```

It prints the resolved configuration and the current tokens, and flags anything inconsistent.
`--redact` hides token values before you paste output into an issue; `--json` gives a
machine-readable report with a stable `id` per check and a `remedy.env` map where a fix is
mechanical. More in [operations](operations.md#the-doctor).
