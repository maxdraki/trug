import time

from fastapi.testclient import TestClient

from trug.app import create_app
from trug.auth import SESSION_COOKIE, hash_token
from trug.config import Settings
from tests.test_llm import FakeClient


def _make_settings():
    return Settings.load(
        {
            "TRUG_TOKEN_RING": "tok-ring",
            "TRUG_TOKEN_MCP": "tok-mcp",
            "TRUG_DB_PATH": ":memory:",
        },
        None,
    )


def _sign_in(app) -> None:
    """Seed an enrolled human and a session so the TestClient can authenticate
    via the cookie (the per-user bearer is retired)."""
    auth_repo = app.state.auth_repo
    auth_repo.seed_users(["alice"])
    uid = auth_repo.get_user_by_name("alice")["id"]
    auth_repo.add_credential(uid, "enrich-cred", b"pubkey", 0, None)
    auth_repo.create_session(hash_token("enrich-session"), uid, 60, "test")


def test_lifespan_enrichment_reaches_catalog_and_item():
    """End-to-end: lifespan captures the loop, the items route schedules
    enrichment from the TestClient's worker thread via
    run_coroutine_threadsafe, and the catalog + item row end up enriched.

    This only passes if the full production wiring (lifespan -> capture_loop
    -> schedule -> run_coroutine_threadsafe -> enrich_async) actually
    delivers the coroutine onto the captured loop. If schedule() silently
    dropped the coroutine (e.g. the loop were never captured), the catalog
    entry's icon would stay None forever and the poll loop below would time
    out, failing the assertion.
    """
    settings = _make_settings()
    auth = {}

    with TestClient(create_app(settings)) as client:
        _sign_in(client.app)
        client.cookies.set(SESSION_COOKIE, "enrich-session")
        client.app.state.enricher.client = FakeClient(
            payload={
                "display_name": "Marmite",
                "icon": "soup",
                "category": "Cupboard",
            }
        )

        # "marmite" is not a tier-0 builtin, so it is added with icon None and
        # the LLM tier must fire to enrich it — exercising the loop delivery.
        resp = client.post("/api/items", json={"name": "marmite"}, headers=auth)
        assert resp.status_code == 200

        repo = client.app.state.repo
        deadline = time.monotonic() + 2.0
        entry = repo.catalog_entry("marmite")
        while (entry is None or entry.get("icon") is None) and time.monotonic() < deadline:
            time.sleep(0.05)
            entry = repo.catalog_entry("marmite")

        assert entry is not None
        assert entry["icon"] == "soup"
        assert entry["category"] == "Cupboard"

        listing = client.get("/api/list", headers=auth).json()
        items = listing["active"].get("Cupboard", [])
        matching = [i for i in items if i["name"] == "Marmite"]
        assert matching, listing
        assert matching[0]["icon"] == "soup"
