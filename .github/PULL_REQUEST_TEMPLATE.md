## What & why

<!-- What does this change do, and why? A sentence or two is plenty. Link any related issue. -->

## Checklist

- [ ] **Passes the Saturday-shop test** — this helps a household get the weekly shop done
      (or explain below why it's a bug fix / infrastructure / docs and exempt).
- [ ] Tests added or updated for the change.
- [ ] Both suites are green: `cd server && uv run pytest -q` and `cd web && npx vitest run`.
- [ ] `uv run ruff check` is clean.
- [ ] Docs updated if behaviour or config changed (README / `.env.example` / etc.).
- [ ] No secrets or personal/household-specific data in the diff (tokens, keys, names,
      walk order, accent — those live in config, not code).

## Notes for the reviewer

<!-- Anything worth flagging: a design trade-off, something you're unsure about, or why an
     out-of-scope-looking change is actually a fix. If this is a new feature and you haven't
     already, consider opening an issue first — see CONTRIBUTING.md. -->
