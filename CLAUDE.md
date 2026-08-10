# Trug — build rules

Trug is a self-hosted shared shopping list (FastAPI + SQLite server in `server/`, Vite + Svelte 5 PWA in `web/`). Product vision and non-goals: `trug-build-brief.md`. Current design spec: `docs/superpowers/specs/2026-08-05-trug-v0-v1-design.md`. Read the spec before implementing anything.

## Scope discipline

Every feature idea gets tested against *"does this make Saturday's shop faster?"* If no, it goes to the parking lot. The brief's non-goals (no recipes, accounts, multi-household, native apps, price tracking) are hard boundaries.

## Model roles & orchestration

- **Fable** does planning, design judgment, and verification of acceptance criteria.
- **Opus** coordinates engineering and fans out implementation subagents wherever work parallelizes (e.g. backend API / frontend shell / theming as concurrent tracks once the API contract is fixed).

## Process

- **TDD, always** — use the superpowers:test-driven-development skill; write the failing test before the implementation. `pytest` in `server/`, `vitest` in `web/`.
- **Pre-commit gauntlet** — before every commit, run:
  1. code-simplifier agent over the changed code
  2. silent-failure-hunter agent (error handling, swallowed exceptions, degraded-mode paths)
  3. code-reviewer agent
  4. secrets scan: no tokens, keys, or household-specific values in the diff (`.env.example` only; real config stays in `.env`/`config.yaml`, both gitignored)
- **UI milestones** — verify in a real browser via the Playwright-driven design-review agent: Mocha + Latte, check-off animation, `prefers-reduced-motion`, offline banner. Screenshot evidence, not assertions.
- **Conventional commits** on `main`.

## Hard rules

- No-key mode (`LLM_API_KEY` unset) must pass the full acceptance path — never gate core function on the LLM.
- No SQLite-isms outside the repository layer.
- No hardcoded hex in frontend components — Catppuccin CSS custom properties only.
- LLM failures degrade silently (no icon, category `Other`, heuristic capture split) and never block an add.
- Nothing household-specific baked into code — names, walk order, accent all come from config.

## Running things

- Server: `cd server && uv run pytest` / `uv run uvicorn trug.app:app --reload`
- Web: `cd web && npm test` / `npm run dev`
- Full stack: `docker compose up`

### Spinning up locally to experiment (hot-reload UI, no passkey needed)

For iterating on the UI in a browser without a real passkey ceremony:

1. **Server** (temp DB + pinned machine tokens):
   ```sh
   cd server && TRUG_DB_PATH=/tmp/trugdev/trug.db \
     TRUG_TOKEN_RING=devring TRUG_TOKEN_MCP=devmcp \
     uv run uvicorn trug.app:app --port 8000
   ```
2. **Seed items** across aisles via the ring webhook:
   ```sh
   for x in "lemon" "tuna" "coconut milk" "coffee" "bananas"; do
     curl -s -X POST localhost:8000/api/capture -H "Authorization: Bearer devring" \
       -H 'content-type: application/json' -d "{\"text\":\"$x\"}"; done
   ```
   (An MCP-sourced item: `POST /mcp` with `Bearer devmcp`, `tools/call` → `add_items`.)
3. **Web dev server** (Vite proxies `/api` → `:8000`, HMR for CSS):
   `cd web && npm run dev`
4. **Open** `http://localhost:5173/?token=devmcp` — the `?token=` path signs in as a
   machine principal, so the list renders without WebAuthn. Switch flavour/accent in the
   browser with `document.documentElement.setAttribute('data-flavour','mocha')` /
   `data-accent`.
