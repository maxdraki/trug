from fastapi.testclient import TestClient

from trug.app import create_app
from trug.config import Settings

RING = {"Authorization": "Bearer tok-ring"}


def test_capture_requires_ring_token(client, auth):
    assert client.post("/api/capture", json={"text": "milk"}, headers=auth).status_code == 403


def test_capture_heuristic_split_no_key(client):
    r = client.post("/api/capture", json={"text": "milk, eggs and dog food"}, headers=RING)
    assert r.status_code == 200
    assert [i["name"] for i in r.json()["added"]] == ["Milk", "Eggs", "Dog Food"]
    assert all(i["source"] == "ring" for i in r.json()["added"])


def test_capture_heuristic_yields_iconed_items_keyless(client):
    # No LLM key: the heuristic splits and tier 0 still ices known groceries.
    r = client.post("/api/capture", json={"text": "milk, eggs and bread"}, headers=RING)
    assert r.status_code == 200
    icons = {i["name"]: i["icon"] for i in r.json()["added"]}
    assert icons == {"Milk": "milk", "Eggs": "egg", "Bread": "bread"}


def test_capture_dedups_against_list(client, auth):
    client.post("/api/items", json={"name": "milk"}, headers=auth)
    r = client.post("/api/capture", json={"text": "milk and bread"}, headers=RING)
    assert len(r.json()["added"]) == 2            # milk is the existing item, not a dupe
    body = client.get("/api/list", headers=auth).json()
    assert sum(len(v) for v in body["active"].values()) == 2


def test_capture_reactivates_checked_item(client, auth):
    # A checked item re-added via the ring reactivates rather than duplicating.
    added = client.post("/api/items", json={"name": "coffee"}, headers=auth).json()
    client.patch(f"/api/items/{added['id']}", json={"status": "checked"}, headers=auth)
    r = client.post("/api/capture", json={"text": "coffee"}, headers=RING)
    assert r.status_code == 200
    body = client.get("/api/list", headers=auth).json()
    active = [i for cat in body["active"].values() for i in cat]
    assert len(active) == 1 and active[0]["id"] == added["id"]
    assert body["checked"] == []


def test_capture_empty_text_ok(client):
    assert client.post("/api/capture", json={"text": ""}, headers=RING).json() == {"added": []}


# --- Pebble ring multipart/form-data (the ring does NOT send JSON) ---------


def test_capture_multipart_transcription_adds_items(client):
    """The ring POSTs multipart/form-data; `transcription` is the text and runs
    the same parse/add pipeline (title-cased, tier-0 icons)."""
    r = client.post(
        "/api/capture",
        data={"transcription": "milk and bread", "recordedAt": "1730000000000", "client": "ring"},
        headers=RING,
    )
    assert r.status_code == 200
    icons = {i["name"]: i["icon"] for i in r.json()["added"]}
    assert icons == {"Milk": "milk", "Bread": "bread"}
    assert all(i["source"] == "ring" for i in r.json()["added"])


def test_capture_multipart_audio_only_is_ok_empty(client):
    """Audio-only (no/empty transcription) must never 4xx once authed — 200 with
    an empty add list and a note."""
    audio = ("clip.mp4", b"\x00\x01\x02\x03fake-audio", "audio/mp4")
    r = client.post(
        "/api/capture",
        data={"recordedAt": "1730000000000", "client": "ring"},
        files={"audio": audio},
        headers={**RING, "X-Audio-Size": "11"},
    )
    assert r.status_code == 200
    assert r.json() == {"added": [], "note": "no transcription"}


def test_capture_multipart_empty_transcription_is_ok_empty(client):
    """An explicitly empty transcription string is treated as audio-only."""
    r = client.post(
        "/api/capture",
        data={"transcription": "  ", "recordedAt": "1730000000000", "client": "ring"},
        headers=RING,
    )
    assert r.status_code == 200
    assert r.json() == {"added": [], "note": "no transcription"}


def test_capture_multipart_requires_ring_token(client, auth):
    """Auth matrix is unchanged for the multipart path: a non-ring bearer 403s."""
    r = client.post(
        "/api/capture",
        data={"transcription": "milk", "client": "ring"},
        headers=auth,
    )
    assert r.status_code == 403


def test_static_serving(tmp_path):
    (tmp_path / "index.html").write_text("<h1>Trug PWA</h1>")
    s = Settings.load(
        {
            "TRUG_USERS": "alice,bob",
            "TRUG_TOKEN_ALICE": "tok-alice",
            "TRUG_TOKEN_BOB": "tok-bob",
            "TRUG_TOKEN_RING": "tok-ring",
            "TRUG_TOKEN_MCP": "tok-mcp",
            "TRUG_DB_PATH": ":memory:",
            "TRUG_STATIC_DIR": str(tmp_path),
        },
        None,
    )
    client = TestClient(create_app(s))
    root = client.get("/")
    assert root.status_code == 200
    assert "Trug PWA" in root.text
    assert client.get("/healthz").status_code == 200
