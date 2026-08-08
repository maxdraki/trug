"""BYOK LLM configuration: store, provider mapping, precedence, endpoints.

Endpoint tests reuse the passkey helpers from test_authn to obtain a real
session cookie (registration via the faked ceremony seam), since the config
routes are session-cookie only.
"""

import anyio
import pytest

from trug.llm import (
    AnthropicClient,
    OpenAICompatClient,
    build_client,
    current_config,
    enrich_name,
)
from trug.llm_config import LLMConfigStore
from trug.config import Settings

from tests.test_authn import make_auth_client, _register  # reuse cookie session


# ---------------------------------------------------------------------------
# store: round-trip + encryption
# ---------------------------------------------------------------------------


def test_store_roundtrip_plaintext():
    store = LLMConfigStore(":memory:", secret=None)
    assert store.get() is None
    store.save("openai", "sk-secret-1234", "gpt-4o-mini", None)
    cfg = store.get()
    assert cfg["provider"] == "openai"
    assert cfg["api_key"] == "sk-secret-1234"
    assert cfg["model"] == "gpt-4o-mini"
    assert store.encrypts is False


def test_store_encrypts_when_secret_set():
    store = LLMConfigStore(":memory:", secret="a-household-secret")
    store.save("anthropic", "sk-ant-topsecret", "claude-haiku-4-5", None)
    # The raw stored value is ciphertext, not the key.
    raw = store._conn.execute(
        "SELECT api_key, encrypted FROM llm_config WHERE id = 1"
    ).fetchone()
    assert raw["encrypted"] == 1
    assert "topsecret" not in raw["api_key"]
    # But get() decrypts it back for server-side use.
    assert store.get()["api_key"] == "sk-ant-topsecret"
    assert store.encrypts is True


def test_store_clear_removes_row():
    store = LLMConfigStore(":memory:", secret=None)
    store.save("ollama", None, "llama3.2", "http://localhost:11434/v1")
    store.clear()
    assert store.get() is None


def test_store_save_overwrites_single_row():
    store = LLMConfigStore(":memory:", secret=None)
    store.save("openai", "k1", "gpt-4o-mini", None)
    store.save("gemini", "k2", "gemini-2.0-flash", None)
    count = store._conn.execute("SELECT COUNT(*) AS n FROM llm_config").fetchone()["n"]
    assert count == 1
    assert store.get()["provider"] == "gemini"


def test_store_flags_unreadable_key_when_secret_gone(caplog):
    """A row encrypted under a now-missing secret decrypts to None, is flagged
    ``unreadable_key``, and logs a warning distinguishing the rotated-secret cause."""
    enc = LLMConfigStore(":memory:", secret="the-old-secret")
    enc.save("anthropic", "sk-ant-topsecret", "claude-haiku-4-5", None)
    row = enc._conn.execute("SELECT * FROM llm_config WHERE id = 1").fetchone()

    # Re-open the same DB with NO secret (secret was removed/rotated away).
    gone = LLMConfigStore(":memory:", secret=None)
    gone._conn.execute(
        "INSERT INTO llm_config "
        "(id, provider, api_key, encrypted, model, base_url, updated_at) "
        "VALUES (1, ?, ?, ?, ?, ?, ?)",
        ("anthropic", row["api_key"], 1, "claude-haiku-4-5", None, row["updated_at"]),
    )
    gone._conn.commit()
    with caplog.at_level("WARNING", logger="trug.llm_config"):
        cfg = gone.get()
    assert cfg["api_key"] is None
    assert cfg["unreadable_key"] is True
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "rotated" in msgs or "removed" in msgs
    # The ciphertext/key is never logged.
    assert row["api_key"] not in msgs


def test_store_flags_unreadable_key_on_corrupt_ciphertext(caplog):
    store = LLMConfigStore(":memory:", secret="a-household-secret")
    store.save("anthropic", "sk-ant-topsecret", "claude-haiku-4-5", None)
    # Corrupt the stored ciphertext in place.
    store._conn.execute(
        "UPDATE llm_config SET api_key = ? WHERE id = 1", ("not-a-valid-token",)
    )
    store._conn.commit()
    with caplog.at_level("WARNING", logger="trug.llm_config"):
        cfg = store.get()
    assert cfg["api_key"] is None
    assert cfg["unreadable_key"] is True
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "corrupt" in msgs.lower()
    assert "not-a-valid-token" not in msgs


def test_store_readable_key_not_flagged_unreadable():
    store = LLMConfigStore(":memory:", secret="a-household-secret")
    store.save("anthropic", "sk-ant-ok", "claude-haiku-4-5", None)
    assert store.get()["unreadable_key"] is False


# ---------------------------------------------------------------------------
# build_client: provider → adapter mapping
# ---------------------------------------------------------------------------


def test_build_client_anthropic():
    c = build_client({"provider": "anthropic", "api_key": "k", "model": "m"})
    assert isinstance(c, AnthropicClient)
    assert c.model == "m"


def test_build_client_gemini_base_url():
    c = build_client({"provider": "gemini", "api_key": "k", "model": "gemini-2.0-flash"})
    assert isinstance(c, OpenAICompatClient)
    assert c.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"


def test_build_client_openai_base_url():
    c = build_client({"provider": "openai", "api_key": "k"})
    assert isinstance(c, OpenAICompatClient)
    assert c.base_url == "https://api.openai.com/v1"


def test_build_client_ollama_no_key():
    c = build_client({"provider": "ollama", "model": "llama3.2",
                      "base_url": "http://localhost:11434/v1"})
    assert isinstance(c, OpenAICompatClient)
    assert c.api_key == "ollama"
    assert c.base_url == "http://localhost:11434/v1"


def test_build_client_custom():
    c = build_client({"provider": "custom", "api_key": "k", "model": "x",
                      "base_url": "https://api.example.com/v1"})
    assert isinstance(c, OpenAICompatClient)
    assert c.base_url == "https://api.example.com/v1"


def test_build_client_none_for_missing_key_or_base():
    assert build_client({"provider": "anthropic"}) is None
    assert build_client({"provider": "ollama", "model": "x"}) is None
    assert build_client(None) is None
    assert build_client({"provider": "bogus", "api_key": "k"}) is None


def test_build_client_defaults_model_per_provider():
    c = build_client({"provider": "anthropic", "api_key": "k"})
    assert c.model == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# current_config: store overrides env
# ---------------------------------------------------------------------------


def test_current_config_prefers_store_over_env():
    settings = Settings.load({"LLM_API_KEY": "env-key", "LLM_MODEL": "claude-haiku-4-5"}, None)
    store = LLMConfigStore(":memory:", secret=None)
    store.save("openai", "store-key", "gpt-4o-mini", None)
    cfg = current_config(settings, store)
    assert cfg["source"] == "settings"
    assert cfg["provider"] == "openai"
    assert cfg["api_key"] == "store-key"


def test_current_config_falls_back_to_env():
    settings = Settings.load({"LLM_API_KEY": "env-key"}, None)
    store = LLMConfigStore(":memory:", secret=None)
    cfg = current_config(settings, store)
    assert cfg["source"] == "env"
    assert cfg["provider"] == "anthropic"
    assert cfg["api_key"] == "env-key"


def test_current_config_env_base_url_is_custom():
    settings = Settings.load(
        {"LLM_API_KEY": "env-key", "LLM_BASE_URL": "https://x/v1"}, None
    )
    cfg = current_config(settings, LLMConfigStore(":memory:", secret=None))
    assert cfg["provider"] == "custom"
    assert cfg["base_url"] == "https://x/v1"


def test_current_config_none_when_nothing_set():
    settings = Settings.load({}, None)
    assert current_config(settings, LLMConfigStore(":memory:", secret=None)) is None


def test_current_config_falls_through_unreadable_row_to_env(caplog):
    """An unreadable stored row (key won't decrypt) must not shadow a working
    env key — enrichment falls back to env, and the fallback is logged."""
    settings = Settings.load(
        {"LLM_API_KEY": "env-key", "LLM_MODEL": "claude-haiku-4-5"}, None
    )
    # Write an anthropic row encrypted under a secret, then re-open with none.
    enc = LLMConfigStore(":memory:", secret="old-secret")
    enc.save("anthropic", "sk-ant-stored", "claude-haiku-4-5", None)
    row = enc._conn.execute("SELECT * FROM llm_config WHERE id = 1").fetchone()
    store = LLMConfigStore(":memory:", secret=None)
    store._conn.execute(
        "INSERT INTO llm_config "
        "(id, provider, api_key, encrypted, model, base_url, updated_at) "
        "VALUES (1, ?, ?, 1, ?, ?, ?)",
        ("anthropic", row["api_key"], "claude-haiku-4-5", None, row["updated_at"]),
    )
    store._conn.commit()

    with caplog.at_level("WARNING", logger="trug.llm"):
        cfg = current_config(settings, store)
    assert cfg["source"] == "env"
    assert cfg["api_key"] == "env-key"
    assert any("fall" in r.getMessage().lower() for r in caplog.records)


def test_enricher_uses_env_key_when_stored_row_unreadable():
    """End-to-end of C2: a corrupt/unreadable row + a valid env key builds an
    enricher client from the env key, not the broken row."""
    from trug.llm import Enricher, build_client
    from trug.repo import Repository
    from trug.bus import EventBus

    settings = Settings.load(
        {"LLM_API_KEY": "env-key", "LLM_MODEL": "claude-haiku-4-5"}, None
    )
    store = LLMConfigStore(":memory:", secret="a-secret")
    store.save("anthropic", "sk-ant-stored", "claude-haiku-4-5", None)
    # Corrupt the ciphertext so the row is unreadable.
    store._conn.execute(
        "UPDATE llm_config SET api_key = ? WHERE id = 1", ("garbage",)
    )
    store._conn.commit()

    client = build_client(current_config(settings, store))
    assert isinstance(client, AnthropicClient)
    assert client.api_key == "env-key"


# ---------------------------------------------------------------------------
# enrich_name: shared prompt path
# ---------------------------------------------------------------------------


class FakeClient:
    def __init__(self, payload=None, exc=None):
        self.payload, self.exc = payload, exc
        self.model = "fake"
        self.seen_timeout = None

    async def complete_json(self, system, user, *, timeout=None):
        self.seen_timeout = timeout
        if self.exc:
            raise self.exc
        return self.payload


def test_enrich_name_validates_icon_and_category():
    client = FakeClient(payload={"display_name": "Milk", "icon": "milk",
                                 "category": "Dairy & Eggs"})
    out = anyio.run(enrich_name, client, "milk", ["Dairy & Eggs", "Other"])
    assert out == {"display_name": "Milk", "icon": "milk", "category": "Dairy & Eggs"}


# ---------------------------------------------------------------------------
# endpoints: GET / PUT / test / DELETE (session-cookie only)
# ---------------------------------------------------------------------------


def _fake_enricher_client(app, payload=None, exc=None):
    """Swap in a fake LLM client so /test hits no network."""
    from trug.routes import llm as llm_routes

    def fake_build(cfg):
        return FakeClient(payload=payload, exc=exc) if cfg else None

    return fake_build, llm_routes


def test_get_llm_config_source_none(monkeypatch):
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    r = c.get("/auth/llm-config")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "none"
    assert body["configured"] is False
    assert body["key_hint"] is None


def test_llm_config_requires_session_cookie():
    c, _ = make_auth_client()
    # Anonymous and bearer are both rejected (session-only).
    assert c.get("/auth/llm-config").status_code == 401
    for tok in ("tok-mcp", "tok-ring", "tok-alice"):
        r = c.get("/auth/llm-config", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401, tok


def test_put_llm_config_persists_and_never_leaks_key(monkeypatch):
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    r = c.put(
        "/auth/llm-config",
        json={"provider": "openai", "api_key": "sk-secret-9876", "model": "gpt-4o-mini"},
        headers={"origin": "http://localhost:8000"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "openai"
    assert body["source"] == "settings"
    assert body["configured"] is True
    assert body["key_hint"] == "••••9876"
    # The full key is never in the response body.
    assert "sk-secret-9876" not in r.text


def test_put_keeps_stored_key_when_omitted(monkeypatch):
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    headers = {"origin": "http://localhost:8000"}
    c.put("/auth/llm-config",
          json={"provider": "openai", "api_key": "sk-keepme-1111", "model": "gpt-4o-mini"},
          headers=headers)
    # Second PUT omits the key (only changes the model) — stored key is kept.
    r = c.put("/auth/llm-config",
              json={"provider": "openai", "model": "gpt-4o"},
              headers=headers)
    assert r.status_code == 200
    assert app.state.llm_store.get()["api_key"] == "sk-keepme-1111"
    assert r.json()["model"] == "gpt-4o"


def test_put_validates_provider_and_required_fields(monkeypatch):
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    headers = {"origin": "http://localhost:8000"}
    assert c.put("/auth/llm-config", json={"provider": "bogus"},
                 headers=headers).status_code == 422
    # Cloud provider without a key.
    assert c.put("/auth/llm-config", json={"provider": "anthropic"},
                 headers=headers).status_code == 422
    # Ollama without base_url.
    assert c.put("/auth/llm-config", json={"provider": "ollama", "model": "llama3.2"},
                 headers=headers).status_code == 422


def test_put_reloads_enricher(monkeypatch):
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    assert app.state.enricher.client is None  # no env key configured
    c.put("/auth/llm-config",
          json={"provider": "anthropic", "api_key": "sk-ant-1234"},
          headers={"origin": "http://localhost:8000"})
    # reload() rebuilt the client from the freshly saved config.
    assert isinstance(app.state.enricher.client, AnthropicClient)
    assert app.state.enricher.client.api_key == "sk-ant-1234"


def test_delete_reverts_to_env(monkeypatch):
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    headers = {"origin": "http://localhost:8000"}
    c.put("/auth/llm-config",
          json={"provider": "anthropic", "api_key": "sk-ant-1234"}, headers=headers)
    r = c.delete("/auth/llm-config", headers=headers)
    assert r.status_code == 200
    assert r.json()["source"] == "none"
    assert app.state.llm_store.get() is None
    assert app.state.enricher.client is None


def test_test_endpoint_success(monkeypatch):
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    from trug.routes import llm as llm_routes
    monkeypatch.setattr(
        llm_routes, "build_client",
        lambda cfg: FakeClient(payload={"display_name": "Milk", "icon": "milk",
                                        "category": "Dairy & Eggs"}) if cfg else None,
    )
    r = c.post("/auth/llm-config/test",
               json={"provider": "openai", "api_key": "sk-x"},
               headers={"origin": "http://localhost:8000"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["result"] == {"icon": "milk", "category": "Dairy & Eggs"}


def test_test_endpoint_failure_surfaces_detail_without_key(monkeypatch):
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    from trug.routes import llm as llm_routes
    secret = "sk-verysecret-should-not-appear"
    monkeypatch.setattr(
        llm_routes, "build_client",
        lambda cfg: FakeClient(exc=RuntimeError(f"401 unauthorized for {secret}")) if cfg else None,
    )
    r = c.post("/auth/llm-config/test",
               json={"provider": "openai", "api_key": secret},
               headers={"origin": "http://localhost:8000"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "401 unauthorized" in body["detail"]
    assert secret not in body["detail"]  # key scrubbed from the detail


def test_test_endpoint_falls_back_to_stored_key(monkeypatch):
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    headers = {"origin": "http://localhost:8000"}
    c.put("/auth/llm-config",
          json={"provider": "openai", "api_key": "sk-stored-key"}, headers=headers)
    from trug.routes import llm as llm_routes
    seen = {}

    def fake_build(cfg):
        seen["api_key"] = cfg.get("api_key") if cfg else None
        return FakeClient(payload={"display_name": "Milk", "icon": "milk",
                                   "category": "Dairy & Eggs"})

    monkeypatch.setattr(llm_routes, "build_client", fake_build)
    # Test with the key omitted — the stored key is used.
    r = c.post("/auth/llm-config/test", json={"provider": "openai"}, headers=headers)
    assert r.status_code == 200
    assert seen["api_key"] == "sk-stored-key"


def test_test_endpoint_uses_long_timeout(monkeypatch):
    """I2: the test path must pass the longer TEST_TIMEOUT to the client, not
    the 2.0s runtime timeout."""
    from trug.routes import llm as llm_routes
    from trug.llm import TEST_TIMEOUT

    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    seen = FakeClient(payload={"display_name": "Milk", "icon": "milk",
                               "category": "Dairy & Eggs"})
    monkeypatch.setattr(llm_routes, "build_client", lambda cfg: seen if cfg else None)
    r = c.post("/auth/llm-config/test",
               json={"provider": "openai", "api_key": "sk-x"},
               headers={"origin": "http://localhost:8000"})
    assert r.status_code == 200
    assert seen.seen_timeout == TEST_TIMEOUT


def test_test_endpoint_renders_timeout_distinctly(monkeypatch):
    """A provider timeout renders a friendly, timeout-specific detail, not a
    bare ReadTimeout."""
    import httpx
    from trug.routes import llm as llm_routes

    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    monkeypatch.setattr(
        llm_routes, "build_client",
        lambda cfg: FakeClient(exc=httpx.ReadTimeout("timed out")) if cfg else None,
    )
    r = c.post("/auth/llm-config/test",
               json={"provider": "ollama", "model": "llama3.2",
                     "base_url": "http://localhost:11434/v1"},
               headers={"origin": "http://localhost:8000"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "timed out after 20s" in body["detail"]
    assert "ReadTimeout" not in body["detail"]


def test_test_endpoint_logs_failure(monkeypatch, caplog):
    """M1: a failed test logs server-side (scrubbed) — the key never appears."""
    from trug.routes import llm as llm_routes

    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    secret = "sk-should-not-be-logged"
    monkeypatch.setattr(
        llm_routes, "build_client",
        lambda cfg: FakeClient(exc=RuntimeError(f"boom {secret}")) if cfg else None,
    )
    with caplog.at_level("WARNING", logger="trug.routes.llm"):
        c.post("/auth/llm-config/test",
               json={"provider": "openai", "api_key": secret},
               headers={"origin": "http://localhost:8000"})
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings, "expected the test failure to be logged"
    joined = " ".join(r.getMessage() for r in warnings)
    assert "llm test failed" in joined
    assert secret not in joined


def test_test_endpoint_env_source_tests_env_key(monkeypatch):
    """Code-review: with an env-sourced config and no store row, testing with
    the key omitted falls back to the effective (env) key, not None."""
    from trug.routes import llm as llm_routes

    c, app = make_auth_client(env={"LLM_API_KEY": "env-key", "LLM_MODEL": "claude-haiku-4-5"})
    _register(c, app, monkeypatch)
    seen = {}

    def fake_build(cfg):
        seen["api_key"] = cfg.get("api_key") if cfg else None
        return FakeClient(payload={"display_name": "Milk", "icon": "milk",
                                   "category": "Dairy & Eggs"}) if cfg else None

    monkeypatch.setattr(llm_routes, "build_client", fake_build)
    r = c.post("/auth/llm-config/test",
               json={"provider": "anthropic"},
               headers={"origin": "http://localhost:8000"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert seen["api_key"] == "env-key"


def test_short_key_hint_is_all_bullets(monkeypatch):
    """M3: a key of length <= 4 reveals nothing — all bullets."""
    from trug.routes.llm import _key_hint

    assert _key_hint("abcd") == "••••"
    assert _key_hint("ab") == "••"
    assert _key_hint("abcde") == "••••bcde"  # >4: last four revealed as before


def test_get_surfaces_unreadable_key(monkeypatch):
    """C1: an unreadable stored row surfaces unreadable_key in the public shape."""
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    store = app.state.llm_store
    # Save encrypted-looking row we can't decrypt (no secret configured here, so
    # write an encrypted=1 row directly).
    store._conn.execute(
        "INSERT INTO llm_config "
        "(id, provider, api_key, encrypted, model, base_url, updated_at) "
        "VALUES (1, 'anthropic', 'garbage-ciphertext', 1, 'claude-haiku-4-5', NULL, 'now')"
    )
    store._conn.commit()
    r = c.get("/auth/llm-config")
    assert r.status_code == 200
    assert r.json()["unreadable_key"] is True


def test_put_rejects_unbuildable_before_persisting(monkeypatch):
    """M2: validation/build happens before persistence; a config that can't
    build a client is not saved."""
    from trug.routes import llm as llm_routes

    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    monkeypatch.setattr(llm_routes, "build_client", lambda cfg: None)
    r = c.put("/auth/llm-config",
              json={"provider": "anthropic", "api_key": "sk-x"},
              headers={"origin": "http://localhost:8000"})
    assert r.status_code == 422
    # Nothing persisted.
    assert app.state.llm_store.get() is None


# ---------------------------------------------------------------------------
# endpoint: POST /llm-config/models (live model listing)
# ---------------------------------------------------------------------------


def test_models_endpoint_returns_filtered_ids(monkeypatch):
    from trug.routes import llm as llm_routes

    c, app = make_auth_client()
    _register(c, app, monkeypatch)

    async def fake_list(cfg):
        assert cfg["provider"] == "gemini"
        return ["gemini-3.6-flash", "gemini-2.5-flash"], {"gemini-3.6-flash": "Gemini 3.6 Flash"}

    monkeypatch.setattr(llm_routes, "list_models_with_labels", fake_list)
    r = c.post("/auth/llm-config/models",
               json={"provider": "gemini", "api_key": "g-key"},
               headers={"origin": "http://localhost:8000"})
    assert r.status_code == 200
    assert r.json() == {
        "models": ["gemini-3.6-flash", "gemini-2.5-flash"],
        "labels": {"gemini-3.6-flash": "Gemini 3.6 Flash"},
    }


def test_models_endpoint_requires_session_cookie():
    c, _ = make_auth_client()
    # Anonymous and machine bearers are both rejected (session-only).
    assert c.post("/auth/llm-config/models", json={"provider": "openai"}).status_code == 401
    for tok in ("tok-mcp", "tok-ring", "tok-alice"):
        r = c.post("/auth/llm-config/models", json={"provider": "openai"},
                   headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401, tok


def test_models_endpoint_falls_back_to_stored_key(monkeypatch):
    from trug.routes import llm as llm_routes

    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    headers = {"origin": "http://localhost:8000"}
    c.put("/auth/llm-config",
          json={"provider": "openai", "api_key": "sk-stored-key"}, headers=headers)
    seen = {}

    async def fake_list(cfg):
        seen["api_key"] = cfg.get("api_key")
        return ["gpt-4o"], {}

    monkeypatch.setattr(llm_routes, "list_models_with_labels", fake_list)
    # Body omits the key — the stored key is reused.
    r = c.post("/auth/llm-config/models", json={"provider": "openai"}, headers=headers)
    assert r.status_code == 200
    assert seen["api_key"] == "sk-stored-key"


def test_models_endpoint_error_returns_empty_and_scrubs_key(monkeypatch, caplog):
    from trug.routes import llm as llm_routes

    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    secret = "sk-verysecret-not-in-response"

    async def fake_list(cfg):
        raise RuntimeError(f"401 unauthorized for {secret}")

    monkeypatch.setattr(llm_routes, "list_models_with_labels", fake_list)
    with caplog.at_level("WARNING", logger="trug.routes.llm"):
        r = c.post("/auth/llm-config/models",
                   json={"provider": "openai", "api_key": secret},
                   headers={"origin": "http://localhost:8000"})
    assert r.status_code == 200
    body = r.json()
    assert body["models"] == []
    assert "401 unauthorized" in body["detail"]
    # The key never appears in the response body nor the server log.
    assert secret not in r.text
    joined = " ".join(rec.getMessage() for rec in caplog.records)
    assert "llm model-list failed" in joined
    assert secret not in joined


def test_models_endpoint_renders_timeout_distinctly(monkeypatch):
    import httpx
    from trug.routes import llm as llm_routes

    c, app = make_auth_client()
    _register(c, app, monkeypatch)

    async def fake_list(cfg):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(llm_routes, "list_models_with_labels", fake_list)
    r = c.post("/auth/llm-config/models",
               json={"provider": "ollama", "base_url": "http://localhost:11434/v1"},
               headers={"origin": "http://localhost:8000"})
    assert r.status_code == 200
    body = r.json()
    assert body["models"] == []
    assert "timed out after 20s" in body["detail"]
    assert "ReadTimeout" not in body["detail"]


def test_models_endpoint_requires_origin(monkeypatch):
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    r = c.post("/auth/llm-config/models",
               json={"provider": "anthropic", "api_key": "k"},
               headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_mutations_require_origin(monkeypatch):
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    # A cookie-bearing mutation with a mismatched Origin is refused (CSRF).
    r = c.put("/auth/llm-config",
              json={"provider": "anthropic", "api_key": "k"},
              headers={"origin": "https://evil.example"})
    assert r.status_code == 403
