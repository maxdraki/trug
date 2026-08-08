import anyio, pytest
import httpx
from trug.llm import (
    heuristic_split, Enricher, build_client, env_config,
    _filter_text_models, list_models,
)
from trug.config import Settings
from trug.repo import Repository
from trug.bus import EventBus


# --- list_models: filtering + per-provider wiring ---------------------------

def test_filter_drops_non_text_models():
    ids = [
        "gpt-4o", "text-embedding-3-small", "whisper-1", "tts-1", "dall-e-3",
        "gemini-2.5-flash", "text-embedding-004", "imagen-3.0", "gpt-4o-mini",
    ]
    kept = _filter_text_models(ids)
    assert "gpt-4o" in kept and "gpt-4o-mini" in kept and "gemini-2.5-flash" in kept
    for dropped in ("text-embedding-3-small", "whisper-1", "tts-1", "dall-e-3",
                    "text-embedding-004", "imagen-3.0"):
        assert dropped not in kept


def test_filter_sorts_newest_first_and_dedupes():
    assert _filter_text_models(["gemini-2.5-flash", "gemini-3.6-flash", "gemini-2.5-flash"]) == [
        "gemini-3.6-flash", "gemini-2.5-flash",
    ]


def _mock_transport(captured):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={"data": [
            {"id": "gemini-2.5-flash"},
            {"id": "gemini-3.6-flash"},
            {"id": "text-embedding-004"},
        ]})
    return httpx.MockTransport(handler)


def test_list_models_anthropic_auth_and_filter(monkeypatch):
    captured = {}
    import trug.llm as llm_mod

    real_client = httpx.AsyncClient

    def fake_client(*a, **k):
        return real_client(*a, transport=_mock_transport(captured), **k)

    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", fake_client)
    out = anyio.run(list_models, {"provider": "anthropic", "api_key": "sk-ant-1"})
    assert captured["url"] == "https://api.anthropic.com/v1/models"
    assert captured["headers"]["x-api-key"] == "sk-ant-1"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    # Embedding id filtered; sorted newest-first.
    assert out == ["gemini-3.6-flash", "gemini-2.5-flash"]


def test_list_models_gemini_strips_models_prefix_and_bearer(monkeypatch):
    captured = {}
    import trug.llm as llm_mod

    def handler(request):
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={"data": [
            {"id": "models/gemini-2.5-flash"},
            {"id": "models/gemini-3.6-flash"},
        ]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        llm_mod.httpx, "AsyncClient",
        lambda *a, **k: real_client(*a, transport=httpx.MockTransport(handler), **k),
    )
    out = anyio.run(list_models, {"provider": "gemini", "api_key": "g-key"})
    assert captured["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/openai/models"
    )
    assert captured["headers"]["authorization"] == "Bearer g-key"
    assert out == ["gemini-3.6-flash", "gemini-2.5-flash"]  # "models/" stripped


def test_list_models_tolerates_malformed_payload(monkeypatch):
    import trug.llm as llm_mod

    def handler(request):
        return httpx.Response(200, json={"unexpected": "shape"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        llm_mod.httpx, "AsyncClient",
        lambda *a, **k: real_client(*a, transport=httpx.MockTransport(handler), **k),
    )
    assert anyio.run(list_models, {"provider": "openai", "api_key": "k"}) == []


def test_list_models_custom_no_base_url_returns_empty():
    assert anyio.run(list_models, {"provider": "custom", "api_key": "k"}) == []
    assert anyio.run(list_models, None) == []

def test_heuristic_split():
    assert heuristic_split("milk, dog food and eggs\nbread") == [
        {"name": "milk"}, {"name": "dog food"}, {"name": "eggs"}, {"name": "bread"}]

def test_env_config_none_without_key():
    assert env_config(Settings.load({}, None)) is None
    assert build_client(env_config(Settings.load({}, None))) is None

class FakeClient:
    def __init__(self, payload=None, exc=None): self.payload, self.exc = payload, exc
    async def complete_json(self, system, user, *, timeout=None):
        if self.exc: raise self.exc
        return self.payload

def make_enricher(client):
    return Enricher(client, Repository(":memory:"), EventBus(),
                    ["Dairy & Eggs", "Other"])

def test_parse_falls_back_on_error():
    e = make_enricher(FakeClient(exc=TimeoutError()))
    assert anyio.run(e.parse, "milk and eggs") == [{"name": "milk"}, {"name": "eggs"}]

def test_parse_uses_llm_when_available():
    e = make_enricher(FakeClient(payload=[{"name": "milk", "note": "2 pints"}]))
    assert anyio.run(e.parse, "two pints of milk") == [{"name": "milk", "note": "2 pints"}]

def test_parse_falls_back_on_null_name():
    e = make_enricher(FakeClient(payload=[{"name": None}]))
    assert anyio.run(e.parse, "milk and eggs") == [{"name": "milk"}, {"name": "eggs"}]

def test_parse_json_strips_json_fence():
    from trug.llm import _parse_json
    assert _parse_json('```json\n[{"name": "milk"}]\n```') == [{"name": "milk"}]

def test_parse_json_strips_plain_fence():
    from trug.llm import _parse_json
    assert _parse_json('```\n{"display_name": "Milk"}\n```') == {"display_name": "Milk"}

def test_enrich_writes_catalog_and_never_raises():
    # "marmite" is not a tier-0 builtin, so the LLM tier does the enriching.
    e = make_enricher(FakeClient(payload={"display_name": "Marmite", "icon": "soup",
                                          "category": "Other"}))
    item, _ = e.repo.add_item(None, "marmite", None, "pwa", "alice")
    anyio.run(e.enrich_async, item)
    assert e.repo.catalog_entry("marmite")["icon"] == "soup"

def test_enrich_rejects_slug_outside_vocabulary():
    e = make_enricher(FakeClient(payload={"display_name": "X", "icon": "🥛",
                                          "category": "Other"}))
    item, _ = e.repo.add_item(None, "widget", None, "pwa", "alice")
    anyio.run(e.enrich_async, item)
    assert e.repo.catalog_entry("widget")["icon"] is None

def test_enrich_invalid_category_resolves_other():
    e = make_enricher(FakeClient(payload={"display_name": "X", "icon": "soup",
                                          "category": "Fishmongery"}))
    item, _ = e.repo.add_item(None, "widget", None, "pwa", "alice")
    anyio.run(e.enrich_async, item)
    assert e.repo.catalog_entry("widget")["category"] == "Other"

def test_enrich_noop_without_client():
    e = make_enricher(None)
    item, _ = e.repo.add_item(None, "widget", None, "pwa", "alice")
    anyio.run(e.enrich_async, item)   # must not raise
    assert e.repo.catalog_entry("widget")["icon"] is None

def test_enrich_logs_warning_on_failure(caplog):
    e = make_enricher(FakeClient(exc=RuntimeError("api error: billing issue XYZ")))
    item, _ = e.repo.add_item(None, "widget", None, "pwa", "alice")
    with caplog.at_level("WARNING", logger="trug.llm"):
        anyio.run(e.enrich_async, item)
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings, "expected a warning to be logged"
    assert any("widget" in r.getMessage() for r in warnings)
    assert any("billing issue XYZ" in r.getMessage() for r in warnings)


def test_enrich_stays_quiet_on_success(caplog):
    e = make_enricher(FakeClient(payload={"display_name": "Widget", "icon": "soup",
                                          "category": "Other"}))
    item, _ = e.repo.add_item(None, "widget", None, "pwa", "alice")
    with caplog.at_level("WARNING", logger="trug.llm"):
        anyio.run(e.enrich_async, item)
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_parse_logs_warning_on_failure(caplog):
    e = make_enricher(FakeClient(exc=RuntimeError("api error: rate limited XYZ")))
    with caplog.at_level("WARNING", logger="trug.llm"):
        anyio.run(e.parse, "milk and eggs")
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings, "expected a warning to be logged"
    assert any("rate limited XYZ" in r.getMessage() for r in warnings)


def test_llm_not_invoked_for_builtin_iconed_items():
    import time
    from fastapi.testclient import TestClient
    from trug.app import create_app
    from trug.config import Settings

    class CountingFakeClient:
        def __init__(self):
            self.call_count = 0
        async def complete_json(self, system, user):
            self.call_count += 1
            return {}  # doesn't matter, shouldn't be called

    s = Settings.load({
        "TRUG_DB_PATH": ":memory:",
    }, None)

    app = create_app(s)
    # Override enricher with one that has a counting fake client
    fake_client = CountingFakeClient()
    app.state.enricher = Enricher(
        fake_client, app.state.repo, app.state.bus, s.walk_order
    )
    # Authenticate as an enrolled human via a session cookie (no more per-user bearer).
    from trug.auth import SESSION_COOKIE, hash_token
    app.state.auth_repo.seed_users(["alice"])
    _uid = app.state.auth_repo.get_user_by_name("alice")["id"]
    app.state.auth_repo.add_credential(_uid, "llm-cred", b"pubkey", 0, None)
    app.state.auth_repo.create_session(hash_token("llm-session"), _uid, 60, "test")

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, "llm-session")
        auth = {}
        r = client.post("/api/items", json={"name": "milk"}, headers=auth)
        assert r.status_code == 200
        item = r.json()
        assert item["icon"] == "milk"  # tier-0 builtin icon

        # Wait for any async enrichment attempts
        time.sleep(0.3)

        # Verify the fake client was never called
        assert fake_client.call_count == 0
