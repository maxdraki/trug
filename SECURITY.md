# Security policy

Trug is a self-hosted app that people put on the open internet (behind a Cloudflare
Tunnel, a Tailscale node, or Railway), and it holds real credentials — passkeys, session
cookies, and encrypted-at-rest LLM API keys. So security reports genuinely matter here,
even for a small household project. Thank you for looking.

## Reporting a vulnerability

**Please don't open a public GitHub issue for a security problem.** A public issue tells
everyone about the hole before self-hosters have had a chance to update.

Instead, email **max@illis.co.uk** with the details. Helpful things to include:

- what the issue is and roughly how bad you think it is,
- steps (or a small proof of concept) to reproduce it,
- which part of Trug it touches, if you know.

If you'd like to send it encrypted, just say so in a first plain email — no need to put
anything sensitive in it — and we'll sort out a channel (e.g. a PGP key or another route)
before you send the real report.

## What to expect

Trug is maintained by one person in their spare time, so this is best-effort, not a funded
program — no bug bounty, no SLA. That said:

- I'll aim to **acknowledge your report within about a week**.
- I'll let you know whether I can reproduce it and what I plan to do.
- Once there's a fix, I'm happy to credit you if you'd like (or keep you anonymous — your
  call).

Please give a reasonable amount of time for a fix to ship before disclosing publicly.

## Where the risk lives

If you're poking at Trug, these are the security-sensitive surfaces most worth your
attention:

- **Passkey / WebAuthn auth** (`py_webauthn`) — registration and login ceremonies, the
  bootstrap-token first-user claim, and single-use invite links.
- **The embedded OAuth 2.1 authorization server** — Trug is its *own* authorization server
  for MCP clients (client ID metadata / CIMD, PKCE, the consent screen and token
  endpoints).
- **Bearer / machine tokens** — the ring and token-based MCP clients authenticate with
  static bearer tokens (`/api/capture`, `/mcp`), which are deliberately more limited than a
  human passkey session.
- **Encryption at rest** — BYOK LLM keys are encrypted with Fernet (`TRUG_SECRET`) when
  stored in SQLite. Key handling, masking, and making sure keys never leak into logs or the
  fetch-models response is in scope.
- **Session handling** — session cookies, revocation, and the boundary that keeps
  bearer/machine sessions out of the human-only Settings areas.

Config and deployment mistakes (a mismatched `TRUG_RP_ID`/`TRUG_ORIGIN`, a plaintext key
because `TRUG_SECRET` is unset on a shared DB) are documented in the README rather than
being bugs — but if you find a case where Trug makes the safe path hard or fails silently in
a dangerous way, that's worth reporting too.

## Supported versions

This is a single-branch project. Security fixes land on **`main`** and in the **latest
release** only. There's no back-porting to older tags — if you're self-hosting, track the
latest.
