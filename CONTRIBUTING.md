# Contributing to Trug

Thanks for wanting to help. Trug is a small, deliberately-focused project — a shared
shopping list for one household — so contributing is pretty relaxed. This page covers how to
get a dev environment running, what the tests and lint expect, and the one thing that shapes
what gets merged: the Saturday-shop test.

## The Saturday-shop test (read this first)

Trug does one thing: **make Saturday's shop faster for two people in a household.** Every
feature idea gets held up against *"does this help alice and bob get the weekly shop
done?"* If the answer is no, it goes to the parking lot — no matter how nice the idea is.

The non-goals are firm boundaries, not open questions: no recipes or meal planning, no
passwords or admin tiers (a flat, invite-based passkey roster only), no multi-tenancy, no
native apps, no price tracking or budgets. See the **Philosophy** section of the
[README](README.md) for the full list.

None of this is meant to be discouraging. If you open a PR for something out of scope,
expect a **kind and quick close** with a pointer to why — not a long debate. If you're not
sure whether an idea fits, **open an issue to ask before you build it**. That'll save you
from writing code that can't be merged.

Bug fixes, docs, accessibility improvements, and things that make the existing flow faster
or clearer are always welcome and don't need the same scrutiny.

## Security issues

Please **don't** report security vulnerabilities in a public issue or PR. See
[SECURITY.md](SECURITY.md) for the private disclosure path.

## Setting up a dev environment

Trug is two parts: a FastAPI + SQLite server in `server/` and a Svelte 5 + Vite PWA in
`web/`.

### Server (Python 3.12, `uv`)

```sh
cd server
uv sync
uv run pytest -q          # run the test suite
uv run ruff check         # lint
uv run uvicorn trug.app:app --reload   # run it locally
```

### Web (Svelte 5 + Vite, TypeScript)

```sh
cd web
npm ci
npx vitest run            # run the test suite
npm run dev               # run the dev server
```

### Full stack

```sh
docker compose up
```

Passkeys work on plain `localhost` with no config, so you can develop the full sign-in flow
without a domain or HTTPS. (See the README for why a real deployment needs an HTTPS name.)

## How we work

- **Tests first (TDD).** Write the failing test, then the code that makes it pass. New
  behaviour should come with a test that would fail without it. `pytest` for the server,
  `vitest` for the web.
- **Keep both suites green.** Don't send a PR with failing tests.
- **Ruff clean.** Run `uv run ruff check` (and let it format) before you commit — the server
  should be lint-clean.
- **Conventional Commits.** Use messages like `feat: add aisle reordering`,
  `fix: don't drop items on a signal blip`, `docs: clarify TRUG_ORIGIN`. Keep them scoped
  and readable.
- **No secrets or personal data in the diff.** No tokens, keys, or household-specific values
  (names, walk order, accent colour) baked into code — those come from config. Only
  `.env.example` is checked in; real config stays in `.env` / `config.yaml`, both gitignored.

## Before you open a PR — the quick checklist

- [ ] Tests added or updated for the change.
- [ ] Both suites pass: `uv run pytest -q` and `npx vitest run`.
- [ ] `uv run ruff check` is clean.
- [ ] Commits follow Conventional Commits.
- [ ] Docs updated if behaviour or config changed.
- [ ] No secrets or household-specific values in the diff.
- [ ] If it's a feature, it passes the Saturday-shop test (or it's a fix / infrastructure).

That's it. Thanks again — a faster Saturday shop for someone's household is the whole point.
