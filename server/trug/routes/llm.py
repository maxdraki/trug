"""In-app BYOK LLM configuration routes (Settings → AI enrichment).

Session-cookie only: a signed-in human configures the enrichment provider, key,
model and base url; a live test runs one real hello-world enrichment before the
config is trusted. Machine/legacy bearers (ring/mcp/pwa-token) are 401 here —
the key and its provider are a human-operator concern, mirroring
/auth/connections. Every mutation is Origin-guarded like the other cookie
/auth routes.

The full api key is never returned to any client — only a masked hint
(``••••1234``) and whether one is configured.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from trug.llm import (
    CLOUD_PROVIDERS,
    DEFAULT_MODELS,
    PROVIDERS,
    TEST_TIMEOUT,
    build_client,
    current_config,
    enrich_name,
    list_models_with_labels,
)
from trug.routes.authn import _session_only, csrf_guard

logger = logging.getLogger("trug.routes.llm")

router = APIRouter(prefix="/auth")

_TEST_ITEM = "milk"
_DETAIL_MAX = 300


class LLMConfigBody(BaseModel):
    provider: str
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None


def _key_hint(key: str | None) -> str | None:
    """A masked hint for a key: the last four behind bullets, never more. A key
    of four chars or fewer reveals nothing — all bullets — so a short/toy key
    isn't handed back near-whole."""
    if not key:
        return None
    if len(key) > 4:
        return "••••" + key[-4:]
    return "•" * len(key)


def _scrub(text: str, key: str | None) -> str:
    """Strip any accidental key occurrence from an error detail. Belt-and-braces:
    the key only ever travels in request headers, never in a response body."""
    if key:
        text = text.replace(key, "••••")
    return text


def _unreadable_key(store) -> bool:
    """Whether a stored row exists whose encrypted key no longer decrypts."""
    row = store.get() if store is not None else None
    return bool(row and row.get("unreadable_key"))


def _public_shape(settings, store) -> dict:
    """The GET/PUT response shape: provider/model/base_url + a masked hint and
    the active source — never the full key.

    ``unreadable_key`` is surfaced independently of ``source`` so the UI can warn
    "your saved key can no longer be decrypted — re-enter it" even when
    enrichment has already fallen back to the env config (see current_config)."""
    unreadable = _unreadable_key(store)
    cfg = current_config(settings, store)
    if cfg is None:
        return {
            "provider": None,
            "model": None,
            "base_url": None,
            "configured": False,
            "key_hint": None,
            "source": "none",
            "unreadable_key": unreadable,
        }
    return {
        "provider": cfg["provider"],
        "model": cfg.get("model"),
        "base_url": cfg.get("base_url"),
        "configured": build_client(cfg) is not None,
        "key_hint": _key_hint(cfg.get("api_key")),
        "source": cfg["source"],
        "unreadable_key": unreadable,
    }


def _validate(body: LLMConfigBody, stored: dict | None) -> None:
    """Reject an unknown provider or a config missing what its provider needs.

    An api key that is omitted but already stored keeps the stored key, so the
    key requirement is satisfied by either a fresh key or a stored one.
    """
    if body.provider not in PROVIDERS:
        raise HTTPException(status_code=422, detail="Unknown provider")
    has_stored_key = bool(
        stored and stored.get("provider") == body.provider and stored.get("api_key")
    )
    if body.provider in CLOUD_PROVIDERS and not (body.api_key or has_stored_key):
        raise HTTPException(status_code=422, detail="API key required")
    if body.provider in ("ollama", "custom") and not body.base_url:
        raise HTTPException(status_code=422, detail="Base URL required")


def _candidate(body: LLMConfigBody, fallback: dict | None) -> dict:
    """A config dict from the request body, reusing ``fallback``'s key when the
    body omits one for the same provider (so a test/save doesn't force
    re-entering the key). ``fallback`` is the stored row for a save, or the
    effective current config for a test — the latter lets an env-sourced setup
    be tested against its env key rather than a non-existent store row."""
    api_key = body.api_key
    if not api_key and fallback and fallback.get("provider") == body.provider:
        api_key = fallback.get("api_key")
    return {
        "provider": body.provider,
        "api_key": api_key,
        "model": body.model or DEFAULT_MODELS.get(body.provider, ""),
        "base_url": body.base_url,
    }


@router.get("/llm-config")
def get_llm_config(request: Request, user=Depends(_session_only)):
    return _public_shape(request.app.state.settings, request.app.state.llm_store)


@router.put("/llm-config", dependencies=[Depends(csrf_guard)])
def put_llm_config(
    body: LLMConfigBody, request: Request, user=Depends(_session_only)
):
    store = request.app.state.llm_store
    settings = request.app.state.settings
    stored = store.get()
    _validate(body, stored)
    candidate = _candidate(body, stored)
    # Build+validate a client from the candidate BEFORE persisting, so a config
    # that can't construct one never lands in the store (and a later reload
    # can't contradict a 500). With a usable candidate, the subsequent reload
    # rebuilds from a freshly-written row that decrypts by construction.
    if build_client(candidate) is None:
        raise HTTPException(
            status_code=422, detail="Could not build a client from this configuration."
        )
    store.save(
        candidate["provider"],
        candidate["api_key"],
        candidate["model"],
        candidate["base_url"],
    )
    try:
        request.app.state.enricher.reload()
    except Exception:  # report a reload fault distinctly from a save fault
        logger.warning("enricher reload failed after saving LLM config", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Saved, but couldn't activate enrichment — reload failed."
        )
    return _public_shape(settings, store)


@router.post("/llm-config/test", dependencies=[Depends(csrf_guard)])
async def test_llm_config(
    body: LLMConfigBody, request: Request, user=Depends(_session_only)
):
    store = request.app.state.llm_store
    settings = request.app.state.settings
    # Fall back to the *effective* current config's key (store row or env), so an
    # env-sourced setup tests against its env key instead of a missing store row.
    candidate = _candidate(body, current_config(settings, store))
    key = candidate.get("api_key")
    client = build_client(candidate)
    if client is None:
        return {"ok": False, "detail": "Provider not configured — check the key and base URL."}
    try:
        enriched = await enrich_name(
            client, _TEST_ITEM, settings.walk_order, timeout=TEST_TIMEOUT
        )
    except httpx.TimeoutException:
        logger.warning(
            "llm test timed out provider=%s model=%s after %ss",
            body.provider, candidate.get("model"), TEST_TIMEOUT, exc_info=True,
        )
        return {
            "ok": False,
            "detail": (
                f"timed out after {int(TEST_TIMEOUT)}s — provider was slow; "
                "try again or check the base URL."
            ),
        }
    except Exception as exc:  # surface any provider error to the human
        scrubbed = _scrub(str(exc) or exc.__class__.__name__, key)
        logger.warning(
            "llm test failed provider=%s model=%s: %s",
            body.provider, candidate.get("model"), scrubbed, exc_info=True,
        )
        return {"ok": False, "detail": scrubbed[:_DETAIL_MAX]}
    return {
        "ok": True,
        "detail": f"Working — {_TEST_ITEM} → {enriched['category']}",
        "result": {"icon": enriched["icon"], "category": enriched["category"]},
    }


@router.post("/llm-config/models", dependencies=[Depends(csrf_guard)])
async def list_llm_models(
    body: LLMConfigBody, request: Request, user=Depends(_session_only)
):
    """Fetch the provider's live text-generation model ids for the model picker.

    Session-authed like the rest of /auth/llm-config. The key falls back to the
    effective current config (store row or env) when the body omits one — same
    ``_candidate`` helper as /test — so a listing never forces re-entering a saved
    key. A provider/network error returns ``{models: [], detail}`` (scrubbed of any
    key, logged server-side at warning) rather than a 500."""
    store = request.app.state.llm_store
    settings = request.app.state.settings
    candidate = _candidate(body, current_config(settings, store))
    key = candidate.get("api_key")
    try:
        models, labels = await list_models_with_labels(candidate)
    except httpx.TimeoutException:
        logger.warning(
            "llm model-list timed out provider=%s after %ss",
            body.provider, TEST_TIMEOUT, exc_info=True,
        )
        return {
            "models": [],
            "detail": (
                f"timed out after {int(TEST_TIMEOUT)}s — provider was slow; "
                "try again or check the base URL."
            ),
        }
    except Exception as exc:  # surface any provider error to the human
        scrubbed = _scrub(str(exc) or exc.__class__.__name__, key)
        logger.warning(
            "llm model-list failed provider=%s: %s",
            body.provider, scrubbed, exc_info=True,
        )
        return {"models": [], "detail": scrubbed[:_DETAIL_MAX]}
    return {"models": models, "labels": labels}


@router.delete("/llm-config", dependencies=[Depends(csrf_guard)])
def delete_llm_config(request: Request, user=Depends(_session_only)):
    request.app.state.llm_store.clear()
    request.app.state.enricher.reload()
    return _public_shape(request.app.state.settings, request.app.state.llm_store)
