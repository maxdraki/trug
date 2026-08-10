# Assistants, the ring, and LLM keys

Three optional things that plug into Trug. None of them is required for a working list.

- [Connect Claude or another MCP client](#mcp)
- [The Pebble ring](#the-pebble-ring)
- [Bring your own key](#bring-your-own-key)

The copy-pasteable values for the first two live in **Settings → Connections**, with a reveal
toggle. That screen needs a passkey session, so if you haven't enrolled yet, read the tokens from
your `.env` or the boot banner instead.

## MCP

Trug ships an MCP (Model Context Protocol) server at `/mcp`, so Claude can read and edit the list
directly. "What's on the shopping list?" "Add oat milk."

**claude.ai, Claude Desktop, or Claude Code.** Add a custom connector and give it just the URL:

```
https://your-domain/mcp
```

That's everything. Trug is its own OAuth 2.1 authorization server, so you sign in with your
passkey, approve a short consent screen, and the connector is linked. No client id, no secret, no
token to paste.

**Token-based clients**, including the ring, can't do an interactive OAuth sign-in. They use the
same URL plus a bearer token:

```
Authorization: Bearer <MCP token>
```

The live value is in Settings → Connections, or `TRUG_TOKEN_MCP`.

A `/mcp` that returns 401 in a connector is expected before you complete the OAuth flow — it's
advertising the auth flow, not refusing you.

## The Pebble ring

The Pebble Index 01 drops items onto the list two ways.

**Webhook route** — the ring transcribes, Trug parses:

| Field | Value |
| --- | --- |
| URL | `https://your-domain/api/capture` |
| Method | `POST`, `multipart/form-data` |
| Authorization | `Bearer <ring token>` (`TRUG_TOKEN_RING`) |

The ring POSTs a `transcription` form field. Trug splits it into items and pushes them to every
connected phone over Server-Sent Events, live. Audio parts are read and discarded.

An audio-only clip with no transcription returns `200` and adds nothing. That's deliberate — the
ring retries on real failures, so a transcription-less clip must never look like an error.

**MCP route** — the ring speaks MCP directly:

| Field | Value |
| --- | --- |
| URL | `https://your-domain/mcp` |
| Transport | Streamable HTTP |
| Authorization | `Bearer <MCP token>` (`TRUG_TOKEN_MCP`) |

This is why the static `TRUG_TOKEN_MCP` bearer stays valid alongside the OAuth path — the ring
can't run an interactive flow.

**Anything else** can hit the same webhook with JSON:

```sh
curl -X POST https://your-domain/api/capture \
  -H "Authorization: Bearer $TRUG_TOKEN_RING" \
  -H "Content-Type: application/json" \
  -d '{"text": "milk, eggs and bread"}'
```

## Bring your own key

**Fully useful with no key; sharper with one.** A built-in icon map means common groceries land
already iconed and sorted into the right aisle, and the ring's capture endpoint falls back to a
heuristic splitter — commas, newlines, and the word "and". The list looks and shops well with no
LLM key at all.

Add a key and Trug also untangles natural-language prose into clean items and covers the rarer
things the built-in map doesn't know.

### In the app

The easy path. Sign in, open **Settings → AI enrichment**, pick a provider — Anthropic, Google
Gemini, OpenAI, Ollama, or a custom OpenAI-compatible endpoint — and paste a key.

Hit **Fetch models** and Trug asks your provider for its live list of text-generation models and
drops them into a dropdown, so you pick a current model instead of guessing a stale id. You can
still type one by hand for anything not listed.

Hit **Test connection** and it runs one real enrichment (`milk → 🥛 Dairy & Eggs`) before you
**Save**. The saved config takes effect immediately with no redeploy, and overrides the
environment variables below. **Clear** reverts to the environment, or to nothing.

Only a signed-in human can reach this screen, and the full key is never shown back — just a masked
`••••1234` hint. It doesn't appear in the fetched-model response or the server logs either.

<p align="center">
  <img src="img/settings-ai.png" alt="Trug settings — AI enrichment section: provider, key, a Fetch models button that lists the provider's live models into a dropdown, and a live test connection button" width="320">
</p>

### Or via environment

Set these as the startup default. A config saved in the app still wins over them. Full details in
[configuration](configuration.md#llm-enrichment).

| Variable | Purpose |
| --- | --- |
| `LLM_API_KEY` | API key for your provider. Enables enrichment when present. |
| `LLM_MODEL` | Model id (default `claude-haiku-4-5`). |
| `LLM_BASE_URL` | Point at an OpenAI-compatible endpoint. Switches the wire protocol, not just the host. |
| `TRUG_SECRET` | Encrypts an in-app saved key at rest. |

No key is ever baked into the image. Enrichment is opt-in, and degrades quietly if the provider is
unavailable — you get the built-in icons and an item on the list, which is the part that matters.
