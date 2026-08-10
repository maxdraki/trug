# Changelog

All notable changes to Trug are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.1] - 2026-08-10

### Fixed

- Settings → About showed `0.1.5` on the 0.2.0 release. The in-app version comes from
  `web/package.json` (injected as `__APP_VERSION__`), nothing bumps it automatically, and the
  release runbook never mentioned it — so tagging shipped an image reporting the previous
  release to everyone. The release workflow now refuses to publish when the tag,
  `web/package.json` and `server/pyproject.toml` disagree.
- `server/pyproject.toml` had been `0.1.0` since the first release. All three now move together.

## [0.2.0] - 2026-08-10

### Added

- A one-line installer (`install.sh`): `curl -fsSL .../install.sh | sh`. It writes `~/.trug`,
  generates all four tokens up front, pulls the image, starts it, waits for a real answer on
  `/healthz`, installs the `trug` command, and prints the bootstrap token. It never installs
  Docker — it names your options and stops — and re-running it is the repair path: no live token
  is regenerated and `data/` is left alone. `TRUG_HOME`, `TRUG_PORT`, `TRUG_REF`,
  `TRUG_HEALTH_TIMEOUT`, `TRUG_IMAGE` and `NO_COLOR` override the defaults. A 32-bit Raspberry Pi
  OS is caught before anything is pulled.
- A `trug` command with six verbs: `up`, `down`, `status`, `logs`, `share`, `set-origin`.
  `trug share` prints a LAN link with the MCP token in it plus a terminal QR; `trug set-origin`
  writes `TRUG_ORIGIN`, derives `TRUG_RP_ID`, restarts and checks the result, refusing a bare
  hostname, a plain-`http` origin, a URL with a path, or an IP address. `status` and `set-origin`
  pass `trug-doctor`'s exit code through.
- `trug-doctor` check `origin.host_is_ip`. For loopback-by-IP it hands over the exact fix; for a
  LAN IP it offers no remedy env, because no setting fixes it, and points at `trug share` or a
  real name instead.
- `docs/remote-access.md`, covering how other people actually reach your instance: the
  zero-setup LAN share link, Tailscale Serve, your own domain with a private-address DNS record,
  and Cloudflare Tunnel.
- Raspberry Pi install notes: the 64-bit OS requirement, the `docker` group logout, and keeping
  the database off the SD card.

### Fixed

- The installer's closing message could hand out a VPN address as "on the wifi". Its address
  lookup had drifted from `trug share`'s and was missing the interface scan, so on Linux it fell
  straight through to the routing table — which returns the VPN's source whenever a VPN carries
  the default route. Both now prefer a private address on a real interface.
- A duplicated key in `.env` is resolved the way Compose resolves it — last wins. Both readers
  took the first, so appending a line to override an earlier one (which genuinely works, because
  `env_file` takes the last) made `trug share` hand out a token the running server rejects.
- 32-bit Raspberry Pi OS is caught on a Pi 4/400/CM4, where `arm_64bit=1` is the default and
  `uname -m` reports `armv8l` rather than `armv7l`. 32-bit x86 too.
- `trug set-origin` no longer leaves a world-readable temp file holding all four secrets if it is
  interrupted.
- `trug share` warns, when `ufw` is running on Linux, that ufw is *not* protecting the published
  port. Docker forwards published ports past ufw's rules entirely, so a default-deny policy gives
  a false sense of what is reachable. The `DOCKER-USER` chain is where a real restriction goes.
- `trug share` picks a real private-range address on a real interface before falling back to the
  routing table. The default route's source is the VPN's address whenever a VPN carries it — a
  Tailscale exit node, or a full-tunnel WireGuard — which answers from the box and nowhere else.

- A failed passkey ceremony is no longer reported as the bare string "Registration failed". The
  server returns a distinguishable `Passkey verification failed`, and the gate turns that into the
  likely cause — the address you're on and the origin Trug is configured for disagree — plus the
  command that confirms it. This is the most common first-deploy failure, and it used to arrive
  with no cause and no next step.
- A confirmed server rejection is never relabelled "you're offline". `navigator.onLine` gets stuck
  false on captive portals, and an answer from the server is proof it isn't the network.
- `origin.observed_host_mismatch` no longer warns about being reached on a LAN IP, which is the
  share link working as designed — it used to make `trug status` exit 1 on every healthy install
  and suggest an IP as the RP ID.
- The doctor treats all of `127.0.0.0/8` as loopback, not just the three usual spellings. Debian
  and Raspberry Pi OS map the machine's own hostname to `127.0.1.1`, which was being diagnosed as
  a remote address needing HTTPS.
- The release workflow refuses to publish under an image name that doesn't match the one
  `docker-compose.yml` and `install.sh` send users to, so a release from a fork can't go green
  while changing nothing for anyone.
- The image carries its own OCI labels, including the commit it was built from. They previously
  came from the base image and named `astral-sh/uv`, so there was no way to tell what a running
  container actually contained.

### Changed

- The sign-in gate now says *why* a passkey can't be created instead of blaming the browser: an
  insecure address points you at `trug share` and notes the "Not Secure" chip is expected; an IP
  address tells you to open `localhost`. On a blocked address it no longer starts a claim ceremony
  it can't finish, and the address explanation outranks the "this trug hasn't been claimed yet"
  copy.
- `rp_id.matches_origin` no longer recommends an IP address as the RP ID, which was advice the
  next check failed you for taking.
- Docs lead with the one-liner: `docs/install.md` gains a **One command** section ahead of Railway
  and Compose, remote access rung one is now `trug share`, and the Pi's SSH tunnel is demoted to a
  footnote — the trial path needs no passkey, so it's no longer a load-bearing step.
- Restructured the README around the ways in — the one-liner, Railway, Docker, and getting the
  rest of the household on it — and moved the reference material into `docs/`: install (now
  including a Raspberry Pi section), remote access, configuration, operations, integrations, and
  troubleshooting. `docs/restore.md` is folded into `docs/operations.md`.

## [0.1.0] - 2026-08-08

### Added

- Offline-first, installable PWA shopping list — one screen grouped into
  store-order aisles, with optimistic updates that keep working with no signal
  and sync when you're back.
- Passkey (WebAuthn) sign-in with an invite-based household roster: no
  passwords, no fixed user list. A one-time bootstrap token claims the first
  account; everyone else joins through a single-use invite link and creates a
  passkey.
- A built-in OAuth 2.1 authorization server plus an MCP server at `/mcp`, so
  assistants like Claude can read and edit the list — link a connector with just
  the URL, no client id or secret to paste.
- Voice-ring capture webhook at `/api/capture` (multipart or JSON) for the
  Pebble Index 01, parsing spoken items straight onto the list and broadcasting
  them to connected phones over Server-Sent Events.
- Bring-your-own-key LLM enrichment (item icons + aisle grouping), fully
  optional — a first-class no-key mode uses a built-in icon map, and the ring
  falls back to a heuristic splitter, so nothing core depends on an LLM.
- Catppuccin theming across all four flavours, with Mocha/Latte following the
  system light/dark preference, plus a configurable accent colour.
- An in-process rate limiter on the sensitive auth endpoints (bootstrap claim,
  passkey login/register, invite redemption, OAuth token), with an explicit
  trusted-proxy-hops boundary.
- The `trug-doctor` diagnostic and recovery CLI — checks config, prints tokens,
  and mints invites or re-opens the first-user claim for lockout recovery
  (`--json`, `--redact` supported).
- Single-container deployment via Docker, Docker Compose, and Railway, with a
  multi-arch (`amd64` + `arm64`) image published to GHCR on tagged releases.

[Unreleased]: https://github.com/maxdraki/trug/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/maxdraki/trug/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/maxdraki/trug/compare/v0.1.5...v0.2.0
[0.1.0]: https://github.com/maxdraki/trug/releases/tag/v0.1.0
