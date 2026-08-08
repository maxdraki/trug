def test_list_includes_other_when_walk_order_omits_it():
    from fastapi.testclient import TestClient
    from trug.app import create_app
    from trug.config import Settings

    from trug.auth import SESSION_COOKIE, hash_token

    s = Settings.load({"TRUG_DB_PATH": ":memory:"}, None)
    s.walk_order = ["Fruit & Veg", "Dairy & Eggs"]  # no "Other"
    app = create_app(s)
    app.state.auth_repo.seed_users(["alice"])
    uid = app.state.auth_repo.get_user_by_name("alice")["id"]
    app.state.auth_repo.create_session(hash_token("s"), uid, 60, "test")
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE, "s")
    auth = {}
    c.post("/api/items", json={"name": "mystery thing"}, headers=auth)
    body = c.get("/api/list", headers=auth).json()
    assert "Other" in body["active"]
    assert body["active"]["Other"][0]["name"] == "Mystery Thing"


def test_add_check_clear_roundtrip(client, auth):
    r = client.post("/api/items", json={"name": "Milk"}, headers=auth)
    item = r.json()
    assert r.status_code == 200 and r.headers["x-created"] == "true"
    assert client.patch(f"/api/items/{item['id']}", json={"status": "checked"}, headers=auth).status_code == 200
    assert client.post("/api/list/clear-checked", headers=auth).json() == {"cleared": 1}


def test_list_grouped_in_walk_order(client, auth):
    # milk is a tier-0 builtin → grouped under its aisle immediately.
    client.post("/api/items", json={"name": "milk"}, headers=auth)
    # an unknown item stays in Other until the LLM tier (if any) enriches it.
    client.post("/api/items", json={"name": "mystery thing"}, headers=auth)
    body = client.get("/api/list", headers=auth).json()
    assert body["active"]["Dairy & Eggs"][0]["name"] == "Milk"
    assert body["active"]["Other"][0]["name"] == "Mystery Thing"
    assert body["checked"] == []


def test_dedup_returns_existing(client, auth):
    a = client.post("/api/items", json={"name": "milk"}, headers=auth).json()
    r = client.post("/api/items", json={"name": "MILK", "note": "2"}, headers=auth)
    assert r.headers["x-created"] == "false" and r.json()["id"] == a["id"]


def test_patch_missing_404(client, auth):
    assert client.patch("/api/items/nope", json={"status": "checked"}, headers=auth).status_code == 404


def test_source_and_added_by_recorded(client, auth):
    item = client.post("/api/items", json={"name": "milk"}, headers=auth).json()
    assert item["source"] == "pwa" and item["added_by"] == "alice"


def test_patch_invalid_status_422(client, auth):
    item = client.post("/api/items", json={"name": "milk"}, headers=auth).json()
    r = client.patch(f"/api/items/{item['id']}", json={"status": "banana"}, headers=auth)
    assert r.status_code == 422


def test_empty_patch_returns_item_without_publishing(client, auth):
    item = client.post("/api/items", json={"name": "milk"}, headers=auth).json()
    bus = client.app.state.bus
    queue = bus.subscribe()
    try:
        r = client.patch(f"/api/items/{item['id']}", json={}, headers=auth)
        assert r.status_code == 200
        assert r.json()["id"] == item["id"]
        assert queue.empty()
    finally:
        bus.unsubscribe(queue)


def test_patch_null_status_keeps_status(client, auth):
    item = client.post("/api/items", json={"name": "milk"}, headers=auth).json()
    r = client.patch(f"/api/items/{item['id']}", json={"status": None}, headers=auth)
    assert r.status_code == 200
    assert r.json()["status"] == item["status"]


def test_item_dict_includes_sort_key(client, auth):
    item = client.post("/api/items", json={"name": "milk"}, headers=auth).json()
    assert isinstance(item["sort_key"], float)


def test_patch_sort_key_reorders_list(client, auth):
    a = client.post("/api/items", json={"name": "apple"}, headers=auth).json()
    b = client.post("/api/items", json={"name": "banana"}, headers=auth).json()
    # Move apple after banana.
    r = client.patch(
        f"/api/items/{a['id']}", json={"sort_key": b["sort_key"] + 1}, headers=auth
    )
    assert r.status_code == 200 and r.json()["sort_key"] == b["sort_key"] + 1
    body = client.get("/api/list", headers=auth).json()
    names = [i["name"] for items in body["active"].values() for i in items]
    assert names.index("Banana") < names.index("Apple")


def test_readd_unenriched_schedules_enrichment(client, auth):
    scheduled = []
    client.app.state.enricher.schedule = lambda item: scheduled.append(item)

    a = client.post("/api/items", json={"name": "mystery thing"}, headers=auth).json()
    assert a["icon"] is None
    scheduled.clear()  # ignore the create-time schedule; only care about the re-add

    r = client.post("/api/items", json={"name": "mystery thing"}, headers=auth)
    assert r.headers["x-created"] == "false"
    assert len(scheduled) == 1
    assert scheduled[0]["id"] == a["id"]


def test_readd_enriched_does_not_schedule(client, auth):
    a = client.post("/api/items", json={"name": "milk"}, headers=auth).json()
    assert a["icon"] == "milk"  # tier-0 builtin, already enriched

    scheduled = []
    client.app.state.enricher.schedule = lambda item: scheduled.append(item)

    r = client.post("/api/items", json={"name": "milk"}, headers=auth)
    assert r.headers["x-created"] == "false"
    assert scheduled == []


def test_patch_rejects_non_finite_sort_key():
    # The PatchItem contract forbids inf/nan sort_keys (allow_inf_nan=False).
    import pytest
    from pydantic import ValidationError

    from trug.routes.items import PatchItem

    PatchItem(sort_key=1.5)  # finite is accepted
    for bad in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ValidationError):
            PatchItem(sort_key=bad)
