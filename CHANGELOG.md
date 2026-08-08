# Changelog

All notable changes to Trug are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/maxdraki/trug/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/maxdraki/trug/releases/tag/v0.1.0
