from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Protocol, runtime_checkable

import httpx

from trug.bus import EventBus
from trug.categories import resolve_category
from trug.config import Settings
from trug.icons import ICON_VOCABULARY
from trug.normalise import normalise
from trug.repo import Repository

logger = logging.getLogger("trug.llm")

_TIMEOUT = 2.0
# The interactive "Test connection" path may cold-start a provider (Gemini,
# Ollama, ...), so it gets a much longer budget than the 2.0s runtime timeout.
TEST_TIMEOUT = 20.0
_MAX_TOKENS = 512
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"

# Split on commas, newlines, and a standalone "and". Kept as data so no
# household specifics ever leak into the heuristic.
_SPLIT_RE = re.compile(r",|\n|\band\b", re.IGNORECASE)

# Matches a leading ```json / ``` fence and its closing ``` so we can strip
# them before json.loads — some models wrap JSON in markdown code fences.
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)


def _parse_json(text: str) -> Any:
    """json.loads ``text``, first stripping any markdown code fence."""
    match = _FENCE_RE.match(text)
    if match:
        text = match.group(1)
    return json.loads(text)

_PARSE_SYSTEM = (
    "You extract shopping-list items from a natural-language capture. "
    "Respond ONLY with a JSON array of objects, each of the form "
    '{"name": "<item>"} with an optional "note" field for quantities or '
    "details. Do not include any prose, only the JSON array."
)

_ENRICH_SYSTEM = (
    "You classify a single shopping-list item for a physical shop. "
    "Respond ONLY with a JSON object of the form "
    '{"display_name": "<tidy name>", "icon": "<one slug from the allowed '
    'icons>", "category": "<one of the allowed categories>"}. '
    "Choose the icon strictly from the allowed icon list and the category "
    "strictly from the allowed categories; if no icon fits use the empty "
    'string "", and if no category fits use the last one. '
    "Output only the JSON object."
)


def heuristic_split(text: str) -> list[dict]:
    """Split free text into item dicts without an LLM.

    Splits on commas, newlines, and the word "and"; strips whitespace;
    drops empties. Always returns ``[{"name": ...}]`` shaped dicts.
    """
    parts = _SPLIT_RE.split(text)
    return [{"name": stripped} for part in parts if (stripped := part.strip())]


@runtime_checkable
class LLMClient(Protocol):
    async def complete_json(
        self, system: str, user: str, *, timeout: float | None = None
    ) -> Any:
        """Send a prompt and return the parsed JSON response.

        ``timeout`` overrides the client's default per-request timeout — used by
        the interactive test path, which tolerates a slow provider cold-start.
        """
        ...


class AnthropicClient:
    """Minimal Anthropic Messages API client returning parsed JSON."""

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    async def complete_json(
        self, system: str, user: str, *, timeout: float | None = None
    ) -> Any:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": _MAX_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        async with httpx.AsyncClient(timeout=timeout or _TIMEOUT) as client:
            resp = await client.post(_ANTHROPIC_URL, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        return _parse_json(data["content"][0]["text"])


class OpenAICompatClient:
    """Minimal OpenAI-compatible chat-completions client returning JSON."""

    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def complete_json(
        self, system: str, user: str, *, timeout: float | None = None
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        url = f"{self.base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=timeout or _TIMEOUT) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        return _parse_json(data["choices"][0]["message"]["content"])


_ERROR_SNIPPET_LEN = 200


def _client_kind(client: LLMClient | None) -> str:
    """A short label identifying the client type for log messages."""
    if isinstance(client, AnthropicClient):
        return "anthropic"
    if isinstance(client, OpenAICompatClient):
        return "openai-compat"
    return type(client).__name__


def _error_snippet(exc: Exception) -> str:
    """A short, log-safe rendering of ``exc``.

    Truncated to ``_ERROR_SNIPPET_LEN`` chars. API error response bodies are
    not secrets and are useful for diagnosis (e.g. billing errors); the API
    key never appears here since it's only ever sent in request headers.
    """
    return str(exc)[:_ERROR_SNIPPET_LEN]


# --- provider registry (BYOK) -----------------------------------------------
# The five providers a human can pick in Settings, each mapped to a concrete
# client + wire protocol. anthropic uses the Messages API; the rest are all
# OpenAI-compatible chat-completions, differing only in base_url / key handling.

PROVIDERS = ("anthropic", "gemini", "openai", "ollama", "custom")

# Cloud providers strictly require an api key; ollama/custom require a base_url.
CLOUD_PROVIDERS = ("anthropic", "gemini", "openai")

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
_OPENAI_BASE_URL = "https://api.openai.com/v1"

_ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"

DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-haiku-4-5",
    # Fallback placeholder only — the live model listing is the real path. Bumped
    # off the stale gemini-2.0-flash (a known 404) to a plausible current id so a
    # no-fetch fallback isn't dead on arrival.
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o-mini",
    "ollama": "llama3.2",
    "custom": "",
}

# Case-insensitive substrings that mark a model id as *not* a text chat model:
# embeddings, speech, audio, image/video generation, rerankers, moderation/safety
# classifiers. Anything matching is dropped from a live listing.
_NON_TEXT_MODEL_RE = re.compile(
    r"embed|embedding|whisper|tts|audio|image|vision-only|dall-e|imagen|veo|"
    r"aqa|rerank|moderation|guard|safety",
    re.IGNORECASE,
)


def _filter_text_models(ids: list[str]) -> list[str]:
    """Keep only text chat model ids, deduped and newest-looking first.

    Drops embedding/speech/audio/image/rerank/safety ids (see
    ``_NON_TEXT_MODEL_RE``). Sorts descending so a higher version/date sorts
    ahead of an older one (a cheap "newest first"); good enough without a
    per-provider recency table.
    """
    kept = [i for i in ids if i and not _NON_TEXT_MODEL_RE.search(i)]
    return sorted(dict.fromkeys(kept), reverse=True)


async def _fetch_models(cfg: dict | None) -> tuple[list[str], dict[str, str]]:
    """Fetch the provider's text-generation model ids AND any human-friendly
    display names the listing carries (Anthropic's ``/v1/models`` entries have a
    ``display_name``; the OpenAI-compat listings usually don't). Returns
    ``(ids, labels)`` where ``labels`` maps id → display name for the kept ids
    that actually had one. The listing is often INCOMPLETE (e.g. Gemini omits
    some working chat models), so callers must still allow a typed-in id."""
    if not cfg:
        return [], {}
    provider = cfg.get("provider")
    key = cfg.get("api_key") or ""
    base_url = (cfg.get("base_url") or "").rstrip("/") or None

    if provider == "anthropic":
        url = _ANTHROPIC_MODELS_URL
        headers = {"x-api-key": key, "anthropic-version": _ANTHROPIC_VERSION}
    elif provider == "gemini":
        url = f"{_GEMINI_BASE_URL}/models"
        headers = {"Authorization": f"Bearer {key}"}
    elif provider == "openai":
        url = f"{_OPENAI_BASE_URL}/models"
        headers = {"Authorization": f"Bearer {key}"}
    elif provider in ("ollama", "custom"):
        if not base_url:
            return [], {}
        url = f"{base_url}/models"
        # ollama needs no real key; only send Bearer when one is present.
        headers = {"Authorization": f"Bearer {key}"} if key else {}
    else:
        return [], {}

    async with httpx.AsyncClient(timeout=TEST_TIMEOUT) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    ids: list[str] = []
    raw_labels: dict[str, str] = {}
    for entry in data.get("data", []) or []:
        model_id = entry.get("id")
        if not model_id:
            continue
        # Gemini's OpenAI-compat listing prefixes ids with "models/"; strip it so
        # the id round-trips straight into a chat-completions request.
        if provider == "gemini" and model_id.startswith("models/"):
            model_id = model_id[len("models/") :]
        ids.append(model_id)
        display = entry.get("display_name") or entry.get("displayName")
        if display:
            raw_labels[model_id] = display
    kept = _filter_text_models(ids)
    labels = {i: raw_labels[i] for i in kept if raw_labels.get(i)}
    return kept, labels


async def list_models(cfg: dict | None) -> list[str]:
    """Provider text-generation model ids, newest-looking first (ids only).

    ``cfg`` keys: ``provider`` (required), ``api_key``, ``base_url``. Uses
    ``TEST_TIMEOUT`` — an interactive call that may cold-start a provider — not
    the 2.0s runtime budget. Raises on a provider/network error; the route turns
    that into a scrubbed ``detail``."""
    ids, _ = await _fetch_models(cfg)
    return ids


async def list_models_with_labels(
    cfg: dict | None,
) -> tuple[list[str], dict[str, str]]:
    """As :func:`list_models`, but also returns id → display-name labels where
    the provider listing supplied them."""
    return await _fetch_models(cfg)


def build_client(cfg: dict | None) -> LLMClient | None:
    """Build an ``LLMClient`` from a config dict, or ``None`` if not buildable.

    ``cfg`` keys: ``provider`` (required), ``api_key``, ``model``, ``base_url``.
    Returns ``None`` for an unknown provider or a config missing what its
    provider needs (a key for cloud providers, a base_url for ollama/custom).
    """
    if not cfg:
        return None
    provider = cfg.get("provider")
    model = cfg.get("model") or DEFAULT_MODELS.get(provider or "", "")
    key = cfg.get("api_key") or ""
    base_url = cfg.get("base_url") or None

    if provider == "anthropic":
        return AnthropicClient(key, model) if key else None
    if provider == "gemini":
        return OpenAICompatClient(key, model, _GEMINI_BASE_URL) if key else None
    if provider == "openai":
        return OpenAICompatClient(key, model, _OPENAI_BASE_URL) if key else None
    if provider == "ollama":
        # No real key; a placeholder keeps the Bearer header well-formed.
        return OpenAICompatClient(key or "ollama", model, base_url) if base_url else None
    if provider == "custom":
        return OpenAICompatClient(key, model, base_url) if base_url else None
    return None


def env_config(settings: Settings) -> dict | None:
    """The env/config-file LLM config as a provider dict, or ``None``.

    A base_url means an OpenAI-compatible ``custom`` endpoint; otherwise the
    env key targets ``anthropic``.
    """
    if not settings.llm_api_key:
        return None
    provider = "custom" if settings.llm_base_url else "anthropic"
    return {
        "provider": provider,
        "api_key": settings.llm_api_key,
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
    }


def current_config(settings: Settings, store) -> dict | None:
    """The active LLM config: the store row if present *and usable*, else the
    env config, else ``None``. The result carries a ``source`` of
    ``settings``/``env``.

    A stored row whose encrypted key no longer decrypts (rotated/removed secret
    or corrupt ciphertext) is *not* authoritative for a provider that needs a
    key: it would otherwise shadow a perfectly good env key and silently kill
    enrichment. Such a row falls through to the env config, with a warning.
    """
    stored = store.get() if store is not None else None
    if stored:
        if stored.get("unreadable_key") and build_client(stored) is None:
            logger.warning(
                "stored LLM key failed to decrypt (TRUG_SECRET rotated/removed "
                "or ciphertext corrupt); falling back to env config for enrichment"
            )
        else:
            return {**stored, "source": "settings"}
    env = env_config(settings)
    if env:
        return {**env, "source": "env"}
    return None


async def enrich_name(
    client: LLMClient, name: str, walk_order: list[str], *, timeout: float | None = None
) -> dict:
    """Run the enrich prompt for a single ``name`` and return the validated
    ``{display_name, icon, category}``. The one place the enrich prompt is
    built, shared by :meth:`Enricher.enrich_async` and the config test endpoint.

    ``icon`` is normalised to a vocabulary slug or ``None``; ``category`` is
    resolved to an allowed aisle (falling back to the last one). ``timeout``,
    when given, overrides the client's per-request timeout (the test path uses a
    longer budget than the 2.0s runtime default).
    """
    allowed = ", ".join(walk_order)
    icons = ", ".join(ICON_VOCABULARY)
    user = (
        f"Item: {name}\n"
        f"Allowed categories: {allowed}\n"
        f"Allowed icons: {icons}"
    )
    kwargs = {} if timeout is None else {"timeout": timeout}
    result = await client.complete_json(_ENRICH_SYSTEM, user, **kwargs)
    icon = result.get("icon")
    if icon not in ICON_VOCABULARY:
        icon = None
    return {
        "display_name": result["display_name"],
        "icon": icon,
        "category": resolve_category(result.get("category"), walk_order),
    }


class Enricher:
    """Parses captures and enriches catalog entries, degrading gracefully.

    Every LLM interaction falls back to a safe local behaviour on a missing
    client, timeout, network error, or malformed response — errors are logged
    at warning level (with enough context to diagnose, but never the API key)
    and never propagated.
    """

    def __init__(
        self,
        client: LLMClient | None,
        repo: Repository,
        bus: EventBus,
        walk_order: list[str],
        settings: Settings | None = None,
        store=None,
    ) -> None:
        self.client = client
        self.repo = repo
        self.bus = bus
        self.walk_order = walk_order
        # Optional references so reload() can rebuild the client from the live
        # config (store overriding env). Absent them, reload() is a no-op.
        self.settings = settings
        self.store = store
        self._loop: asyncio.AbstractEventLoop | None = None

    def reload(self) -> None:
        """Rebuild ``self.client`` from the current config (store over env).

        Called after a successful config save/clear so enrichment picks up the
        new provider/key without a redeploy. No-op when settings/store were not
        wired in (e.g. bare unit-test enrichers)."""
        if self.settings is None:
            return
        self.client = build_client(current_config(self.settings, self.store))

    async def parse(self, text: str) -> list[dict]:
        """Parse a capture into item dicts; heuristic fallback on any error."""
        if self.client is None:
            return heuristic_split(text)
        try:
            result = await self.client.complete_json(_PARSE_SYSTEM, text)
            if isinstance(result, list) and all(
                isinstance(entry, dict)
                and isinstance(entry.get("name"), str)
                and entry["name"].strip()
                for entry in result
            ):
                return result
        except Exception as exc:
            logger.warning(
                "parse via LLM failed (client=%s, model=%s): %s; using heuristic",
                _client_kind(self.client),
                getattr(self.client, "model", "?"),
                _error_snippet(exc),
                exc_info=True,
            )
        return heuristic_split(text)

    async def enrich_async(self, item: dict) -> None:
        """Enrich the catalog entry for ``item``; never raises."""
        name_norm = item.get("name")
        try:
            if self.client is None:
                return
            name_norm = normalise(item["name"], self.repo.known_norms())
            entry = self.repo.catalog_entry(name_norm)
            if entry is not None and entry.get("icon"):
                return
            # Shared enrich prompt path — only accepts a slug from the shared
            # vocabulary (anything else, including "", degrades to no icon ->
            # client monogram fallback) and an allowed category.
            enriched = await enrich_name(self.client, item["name"], self.walk_order)
            display_name = enriched["display_name"]
            icon = enriched["icon"]
            category = enriched["category"]
            self.repo.set_enrichment(name_norm, display_name, icon, category)
            updated = self.repo.update_item(item["id"], icon=icon, category=category)
            self.bus.publish("item_updated", updated or item)
        except Exception as exc:
            logger.warning(
                "enrich failed for %r (client=%s, model=%s): %s",
                name_norm,
                _client_kind(self.client),
                getattr(self.client, "model", "?"),
                _error_snippet(exc),
                exc_info=True,
            )

    def capture_loop(self) -> None:
        """Record the running loop so off-thread scheduling can reach it."""
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

    def schedule_on_add(self, item: dict) -> None:
        """Schedule enrichment after an add-item call, if it might help.

        Called for both a freshly created item and a dedupe/reactivation of
        an existing one — whenever the returned item still has no icon.
        Re-adds are a natural retry point when earlier enrichment attempts
        failed (LLM outage, billing error, ...). The catalog's once-only
        enrichment guard makes over-scheduling harmless: ``enrich_async``
        no-ops if an icon already landed by the time it runs.
        """
        if item.get("icon") is None:
            self.schedule(item)

    def schedule(self, item: dict) -> None:
        """Fire-and-forget enrichment from any thread.

        Mirrors ``EventBus``: when called off the loop thread the coroutine is
        handed to the captured loop via ``run_coroutine_threadsafe``. When no
        loop is available (e.g. tests without a running server) the coroutine
        is discarded silently rather than raising.
        """
        coro = self.enrich_async(item)
        loop = self._loop
        on_loop = False
        if loop is not None:
            try:
                on_loop = asyncio.get_running_loop() is loop
            except RuntimeError:
                on_loop = False
        try:
            if loop is None:
                # No captured loop; try the current thread's loop, else drop.
                asyncio.get_running_loop().create_task(coro)
            elif on_loop:
                loop.create_task(coro)
            else:
                asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            coro.close()
