# Trug — build rules

Trug is a self-hosted shared shopping list (FastAPI + SQLite server in `server/`, Vite + Svelte 5 PWA in `web/`). The product vision and non-goals live in `README.md`; the design system is in `DESIGN.md`. Read them before implementing anything.

## Scope discipline

Every feature idea gets tested against *"does this make Saturday's shop faster?"* If no, it goes to the parking lot. The non-goals (no recipes, no accounts, no multi-household, no native apps, no price tracking) are hard boundaries.

## Process

- **TDD, always** — write the failing test before the implementation. `pytest` in `server/`, `vitest` in `web/`.
- **Before every commit** — both test suites green, `ruff` clean, and scan the diff for secrets: no tokens, keys, or household-specific values (`.env.example` only; real config stays in `.env`/`config.yaml`, both gitignored).
- **UI changes** — verify in a real browser: both Catppuccin themes (Mocha + Latte), the check-off animation, `prefers-reduced-motion`, and the offline banner.
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
