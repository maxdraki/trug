# Hosting Trug

Trug is a shared shopping list for one household: a FastAPI server, a SQLite file and a compiled
Svelte PWA, all in one container. No sidecars, no database service, no queue, and no external
dependency of any kind unless the operator adds an optional LLM key. This page is for people
running a hosting platform, deciding whether Trug fits; to install it on your own box, start at
[install](install.md) instead.

## The image

`ghcr.io/maxdraki/trug`, published for `linux/amd64` and `linux/arm64`. It builds from the
`Dockerfile` at the repository root: a Node stage compiles the PWA, then a
`ghcr.io/astral-sh/uv:python3.12-bookworm-slim` stage installs the server into `/app/.venv`.
Nothing is fetched at run time.

A tag push of `vX.Y.Z` publishes `:vX.Y.Z`, plus `:latest` unless the tag is a prerelease. Before
anything reaches the registry the workflow boots the build, waits for `/healthz`, checks the SPA is
served and runs `trug-doctor`; a failure aborts the release with nothing published. Images carry
SLSA build provenance and OCI labels, including `org.opencontainers.image.revision` (the commit it
was built from) and `.version` (the tag), so `docker image inspect` answers "which code is this?"
without filesystem archaeology.

## Runtime shape

One HTTP port, no TLS of its own — put your ingress in front of it. The entrypoint reads `PORT`
from the environment, falls back to `8000` when it is unset **or empty**, and refuses to start if
the value isn't a whole number. The `EXPOSE 8000` line is documentation and a fallback, not a
binding: **do not hardcode 8000**, inject `PORT` and Trug binds it.

`GET /healthz` returns `200` with `{"status":"ok"}`. It touches no disk and is excluded from the
origin-observation middleware, so healthchecking it costs nothing and can't skew diagnostics; the
image ships its own `HEALTHCHECK` against the same path. Everything else shares that port too: the
PWA at `/`, the JSON API under `/api/`, auth under `/auth/`, MCP at `POST /mcp`, its OAuth
endpoints at `/oauth/*` and `/.well-known/oauth-*`, and the SSE stream at `/api/events`.

**One replica.** State is a SQLite file, and the event bus and auth rate limiter are both
in-process. A second replica would split the household across two databases.

## Users and permissions

The image creates a `trug` user with a pinned uid/gid of `999`, and `docker-entrypoint.sh` branches
on who it starts as:

- **Started as root:** it `mkdir -p`s the data directory, `chown -R`s it to `trug:trug`, then
  `exec gosu trug "$@"`. The app never runs as root. This is the path Docker Compose, Railway and a
  Pi bind mount take, and it is why no manual `chown` appears anywhere in our docs.
- **Started as anything else:** it `exec`s straight through, with no `mkdir` and no `chown`. The
  app runs as whatever uid you gave it.

A platform that runs containers as its own fixed non-root UID is fine — Trug does not require root
and does not care which uid it is — but it inherits the ownership problem the entrypoint would
otherwise have solved for it. **The volume must already be writable by the UID you run as.** The
server creates the database's parent directory itself at start-up if it can, but it cannot chown a
directory it doesn't own. A root-owned volume under a non-root UID fails at the first write, and
`trug-doctor` reports it as `storage.db_path` with the uid it was checking as. Nothing outside the
data directory needs to be writable.

## Persistence

One directory: whatever `TRUG_DB_PATH` points at, `/data/trug.db` in the image. That single SQLite
file (WAL mode) holds the list, the household roster, passkey credentials, sessions and any saved
LLM config. Mount a volume there and you have covered everything. If you don't, Trug still starts
and works — the database lands in the container's writable layer and disappears with the container.
Silent enough that `trug-doctor` checks for it: `storage.data_mount` warns when the data directory
is not a mount point.

## Configuration

All environment variables, all optional at boot. Full reference in
[configuration](configuration.md).

**Required for passkeys on a real hostname** — the defaults only work on `localhost`:

| | |
| --- | --- |
| `TRUG_ORIGIN` | the full origin people load, e.g. `https://trug.example.com` |
| `TRUG_RP_ID` | the bare host, no scheme and no port, e.g. `trug.example.com` |

**Generated when unset**, and this is the part worth reading twice: `TRUG_TOKEN_RING`,
`TRUG_TOKEN_MCP` and `TRUG_BOOTSTRAP_TOKEN` are generated *per process* and printed in the boot
banner. They are not persisted, so every restart mints new ones and logs out any ring or MCP client
holding the old value. If your platform can generate a value once at deploy time and store it as a
variable, do that for all three. (The bootstrap token claims the very first account, then goes
inert.)

**Optional:** `PORT`, `TRUG_DB_PATH`, `TRUG_STATIC_DIR`, `TRUG_SESSION_DAYS`,
`TRUG_RATE_LIMIT_ENABLED`, `TRUG_TRUSTED_PROXY_HOPS`, `TRUG_SECRET`, and the `LLM_*` keys. If your
ingress sits in front of Trug, set `TRUG_TRUSTED_PROXY_HOPS` to the number of hops — the default
`0` ignores `X-Forwarded-For` entirely, so the whole household shares one rate-limit bucket. See
[behind a proxy](configuration.md#behind-a-proxy).

Settings can also come from a `config.yaml` in the working directory (`/app`), with environment
variables taking precedence. One sharp edge if you offer both: an environment variable set to an
*empty* string still counts as set and beats the file. Unset a variable rather than blanking it.

## The one genuinely fiddly thing

`TRUG_ORIGIN` and `TRUG_RP_ID` must match the URL in the browser exactly. WebAuthn binds
credentials to the RP ID, so if they disagree every passkey ceremony fails — no error in the logs,
nothing in the UI, and a perfectly healthy healthcheck. It is the most common way a Trug deployment
ends up broken, and from the outside it looks fine.

If your platform assigns the hostname, inject it: `TRUG_ORIGIN=https://<assigned-host>` and
`TRUG_RP_ID=<assigned-host>` at deploy time removes the whole failure class. If the user later adds
a custom domain, both need updating and existing passkeys re-enrolling — credentials are bound to
the hostname they were created under.

`trug-doctor` (on `PATH` in the image) exists for this. It compares the configured origin against
the hosts the server has actually been reached as, and checks storage, tokens and proxy settings on
the way past. Exit codes are a contract: `0` healthy, `1` warnings, `2` errors. `--json` gives a
report with a stable `id` per check and a `remedy.env` map where the fix is mechanical — enough to
build a "fix it" button on. More in [the doctor](operations.md#the-doctor).

## Footprint and long-lived connections

Small, and here is where the numbers come from. The published image is about 450 MB unpacked on
disk (`docker images`, arm64). An idle container measured ~50 MiB RSS and under 1% of a core via
`docker stats` — one sample on a developer machine, not a benchmark. One uvicorn process, no worker
pool. Disk growth is a shopping list in SQLite: text rows, no blobs, no media, nothing logged to
disk. We haven't run one long enough to quote a year's growth, so treat it as "grows slowly" rather
than a number, give the volume a gigabyte, and let the doctor warn you on low free space.

Each open client holds one `GET /api/events` SSE connection for as long as its tab is open, which
is how the list stays in sync between phones. The server sends a comment ping every 25 seconds of
silence, so an idle timeout above 30 seconds won't cut a healthy stream — but response buffering
breaks sync entirely, and a short idle timeout puts clients in a reconnect loop. Disable buffering
for that path, or for the service. It's one stream per open tab in one household; not a fan-out
workload.

## Networking and outbound

No outbound connections at all in the default configuration. No telemetry, no update checks, no
CDN — fonts, icons and the palette are compiled into the image, so the PWA loads entirely from the
origin that served it. The exception is opt-in: with `LLM_API_KEY` set (or a key saved in Settings
by a signed-in user), Trug calls that provider to guess an icon and an aisle for new items —
`api.anthropic.com` by default, or whatever `LLM_BASE_URL` names. Those calls degrade quietly on
failure and never block an add, so egress restrictions cost function, not availability.

## Backups

Back up the data directory; the one SQLite file is everything. Two caveats worth passing on to your
users: take snapshots with `sqlite3 .backup` rather than `cp`, because a plain copy of a live WAL
database can be stale or broken, and on restore delete the `-wal` and `-shm` sidecars or they will
shadow the file you just put back. Both are written up in [backup](operations.md#backup) and
[restore](operations.md#restore).
