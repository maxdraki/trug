"""Route-level tests for the stateless MCP surface at /mcp.

These drive the JSON-RPC-over-HTTP-POST protocol with the mcp bearer, the
exact shape Claude Code's streamable-http client speaks. Stateless: no
Mcp-Session-Id is required or emitted, and the initialize handshake is
accepted but not mandatory (a bare tools/list works too).
"""

from fastapi.testclient import TestClient

from trug.app import create_app
from trug.config import Settings

MCP = {"Authorization": "Bearer tok-mcp"}


def make_client():
    s = Settings.load(
        {
            "TRUG_USERS": "alice,bob",
            "TRUG_TOKEN_ALICE": "tok-alice",
            "TRUG_TOKEN_BOB": "tok-bob",
            "TRUG_TOKEN_RING": "tok-ring",
            "TRUG_TOKEN_MCP": "tok-mcp",
            "TRUG_DB_PATH": ":memory:",
        },
        None,
    )
    return TestClient(create_app(s))


def rpc(client, method, params=None, id=1, headers=MCP):
    body = {"jsonrpc": "2.0", "method": method}
    if id is not None:
        body["id"] = id
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers)


def call(client, name, arguments=None, headers=MCP):
    r = rpc(
        client,
        "tools/call",
        {"name": name, "arguments": arguments or {}},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["result"]


# --- handshake / protocol -------------------------------------------------


def test_initialize_returns_server_info():
    c = make_client()
    r = rpc(c, "initialize", {"protocolVersion": "2026-07-28", "capabilities": {}})
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["protocolVersion"]
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "trug"


def test_initialized_notification_accepted():
    c = make_client()
    # A JSON-RPC notification (no id) gets a 202 with no body.
    r = rpc(c, "notifications/initialized", id=None)
    assert r.status_code == 202


def test_ping():
    c = make_client()
    assert rpc(c, "ping").json()["result"] == {}


def test_unknown_method_is_jsonrpc_error():
    c = make_client()
    body = rpc(c, "does/not/exist").json()
    assert body["error"]["code"] == -32601


def test_unknown_tool_is_invalid_params():
    c = make_client()
    r = rpc(c, "tools/call", {"name": "nope", "arguments": {}})
    assert r.json()["error"]["code"] == -32602


def test_add_items_missing_name_is_invalid_params():
    c = make_client()
    r = rpc(c, "tools/call", {"name": "add_items", "arguments": {"items": [{"note": "x"}]}})
    assert r.json()["error"]["code"] == -32602


def test_stateless_no_session_id_header():
    c = make_client()
    r = rpc(c, "tools/list")
    assert r.status_code == 200
    assert "mcp-session-id" not in {k.lower() for k in r.headers}


# --- tools/list -----------------------------------------------------------


def test_tools_list_shape():
    c = make_client()
    tools = rpc(c, "tools/list").json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {
        "add_items",
        "get_shopping_list",
        "check_item",
        "uncheck_item",
        "remove_item",
        "suggest_from_history",
    }
    for t in tools:
        assert t["description"]
        assert t["inputSchema"]["type"] == "object"


# --- add_items ------------------------------------------------------------


def test_add_items_batch_returns_ids():
    c = make_client()
    result = call(c, "add_items", {"items": [{"name": "Milk"}, {"name": "Eggs", "note": "dozen"}]})
    added = result["structuredContent"]["added"]
    assert len(added) == 2
    assert all(a["id"] for a in added)
    assert all(a["created"] for a in added)
    assert all(a["source"] == "mcp" for a in added)
    eggs = next(a for a in added if a["name"] == "Eggs")
    assert eggs["note"] == "dozen"


def test_add_items_dedup():
    c = make_client()
    call(c, "add_items", {"items": [{"name": "milk"}]})
    result = call(c, "add_items", {"items": [{"name": "MILK", "note": "2"}]})
    added = result["structuredContent"]["added"]
    assert added[0]["created"] is False


def test_add_items_reactivates_checked_item():
    # Re-adding a checked item via MCP reactivates it (created False), leaving no
    # duplicate in the basket. New semantics — see test_repo reactivation tests.
    c = make_client()
    added = call(c, "add_items", {"items": [{"name": "coffee"}]})["structuredContent"]["added"]
    item_id = added[0]["id"]
    call(c, "check_item", {"name_or_id": item_id})
    result = call(c, "add_items", {"items": [{"name": "coffee"}]})
    reactivated = result["structuredContent"]["added"][0]
    assert reactivated["created"] is False and reactivated["id"] == item_id
    assert reactivated["status"] == "active"
    listing = call(c, "get_shopping_list")["structuredContent"]
    active = [i for cat in listing["active"].values() for i in cat]
    assert len(active) == 1 and listing["checked"] == []


def test_add_items_publishes_on_bus():
    c = make_client()
    bus = c.app.state.bus
    q = bus.subscribe()
    try:
        call(c, "add_items", {"items": [{"name": "milk"}]})
        event = q.get_nowait()
        assert event["event"] == "item_added"
    finally:
        bus.unsubscribe(q)


# --- get_shopping_list ----------------------------------------------------


def test_get_shopping_list_mirrors_api_list():
    c = make_client()
    call(c, "add_items", {"items": [{"name": "milk"}, {"name": "mystery thing"}]})
    result = call(c, "get_shopping_list")
    data = result["structuredContent"]
    assert "active" in data and "checked" in data
    # grouped by aisle, same as /api/list
    assert data["active"]["Dairy & Eggs"][0]["name"] == "Milk"
    assert data["active"]["Other"][0]["name"] == "Mystery Thing"
    # ttl cache hint on the list-shaped result
    assert result["_meta"]["ttlMs"] == 5000


# --- check_item / uncheck_item -------------------------------------------


def test_check_item_fuzzy_match():
    c = make_client()
    call(c, "add_items", {"items": [{"name": "Organic Whole Milk"}]})
    result = call(c, "check_item", {"name_or_id": "milk"})
    assert result["structuredContent"]["matched"]["name"] == "Organic Whole Milk"
    assert result["structuredContent"]["matched"]["status"] == "checked"


def test_check_item_by_id():
    c = make_client()
    added = call(c, "add_items", {"items": [{"name": "milk"}]})["structuredContent"]["added"]
    item_id = added[0]["id"]
    result = call(c, "check_item", {"name_or_id": item_id})
    assert result["structuredContent"]["matched"]["id"] == item_id


def test_check_item_not_found():
    c = make_client()
    result = call(c, "check_item", {"name_or_id": "nonexistent"})
    assert result["isError"] is True


def test_uncheck_item_fuzzy_match():
    c = make_client()
    call(c, "add_items", {"items": [{"name": "milk"}]})
    call(c, "check_item", {"name_or_id": "milk"})
    result = call(c, "uncheck_item", {"name_or_id": "milk"})
    assert result["structuredContent"]["matched"]["status"] == "active"


def test_uncheck_item_not_found_when_active():
    c = make_client()
    # active items are not candidates for uncheck
    call(c, "add_items", {"items": [{"name": "milk"}]})
    result = call(c, "uncheck_item", {"name_or_id": "milk"})
    assert result["isError"] is True


# --- remove_item ----------------------------------------------------------


def test_remove_item_fuzzy():
    c = make_client()
    call(c, "add_items", {"items": [{"name": "milk"}]})
    result = call(c, "remove_item", {"name_or_id": "milk"})
    assert result["structuredContent"]["removed"]["name"] == "Milk"
    listing = call(c, "get_shopping_list")["structuredContent"]
    assert listing["active"] == {}


def test_remove_item_not_found():
    c = make_client()
    result = call(c, "remove_item", {"name_or_id": "ghost"})
    assert result["isError"] is True


# --- suggest_from_history -------------------------------------------------


def test_suggest_excludes_active():
    c = make_client()
    # Build catalog history, then clear the list so entries are "not active".
    call(c, "add_items", {"items": [{"name": "coffee"}, {"name": "bread"}]})
    call(c, "remove_item", {"name_or_id": "coffee"})
    call(c, "remove_item", {"name_or_id": "bread"})
    # Re-add coffee so it IS active; it must be excluded from suggestions.
    call(c, "add_items", {"items": [{"name": "coffee"}]})
    result = call(c, "suggest_from_history")
    names = {s["display_name"] for s in result["structuredContent"]["suggestions"]}
    assert "Bread" in names
    assert "Coffee" not in names


def test_suggest_hint_filters():
    c = make_client()
    call(c, "add_items", {"items": [{"name": "coffee"}, {"name": "bread"}]})
    call(c, "remove_item", {"name_or_id": "coffee"})
    call(c, "remove_item", {"name_or_id": "bread"})
    result = call(c, "suggest_from_history", {"hint": "cof"})
    names = {s["display_name"] for s in result["structuredContent"]["suggestions"]}
    assert names == {"Coffee"}


def test_suggest_has_ttl_meta():
    c = make_client()
    result = call(c, "suggest_from_history")
    assert result["_meta"]["ttlMs"] == 5000


# --- auth matrix ----------------------------------------------------------


def test_no_auth_401():
    c = make_client()
    r = rpc(c, "tools/list", headers={})
    assert r.status_code == 401
    # Discovery: the challenge points OAuth-capable clients at the protected-
    # resource metadata so they can find the authorization flow.
    www = r.headers.get("WWW-Authenticate")
    assert www.startswith("Bearer ")
    assert "resource_metadata=" in www
    assert "/.well-known/oauth-protected-resource/mcp" in www


def test_ring_bearer_403():
    c = make_client()
    r = rpc(c, "tools/list", headers={"Authorization": "Bearer tok-ring"})
    assert r.status_code == 403


def test_wrong_token_401():
    c = make_client()
    r = rpc(c, "tools/list", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
