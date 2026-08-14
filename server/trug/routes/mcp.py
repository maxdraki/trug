"""Stateless MCP server surface at ``/mcp``.

Implemented directly as a FastAPI route rather than via the `mcp` SDK: the
protocol Claude needs is a thin JSON-RPC-over-HTTP-POST request/response
surface (``tools/list`` + ``tools/call``), and the 2026-07-28 spec revision is
stateless-first — no ``initialize`` handshake required, no ``Mcp-Session-Id``,
no long-lived streams.

Compatibility: the endpoint is stateless (never requires or emits a session
id) but still *accepts* the legacy streamable-HTTP handshake frames
(``initialize`` / ``notifications/initialized`` / ``ping``) so today's Claude
Code client, which still speaks the prior revision, connects unchanged. Every
request is self-describing, so a bare ``tools/list`` also works — the 2026-07-28
stateless shape.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from trug import oauth
from trug.auth import Principal, _authenticate_token, _extract_bearer
from trug.categories import resolve_category
from trug.normalise import normalise

router = APIRouter()

logger = logging.getLogger("trug.mcp")

# The protocol revision this server is designed against. Echoed back on
# ``initialize`` when the client does not pin one it also supports.
PROTOCOL_VERSION = "2026-07-28"
SERVER_INFO = {"name": "trug", "version": "2.0"}


def _server_info(origin: str) -> dict:
    """`initialize` serverInfo, including the MCP `icons` field (spec 2025-11-25+)
    so a client that renders server branding shows the Trug logo instead of the
    generic connector icon. `src` points at the same-origin PWA icon Trug already
    serves (a data URI would also be valid); clients trust same-origin icons."""
    return {
        **SERVER_INFO,
        "title": "Trug",
        "websiteUrl": origin,
        "icons": [
            {
                "src": f"{origin}/icons/icon-192.png",
                "mimeType": "image/png",
                "sizes": ["192x192"],
            }
        ],
    }

# List-shaped tool results carry a short client-side cache hint (~5s): the
# household shops together, so a few seconds of staleness is fine and saves
# round-trips.
LIST_TTL_MS = 5000

_CATALOG_FIELDS = ("name_norm", "display_name", "icon", "category", "times_added")


# --- auth -----------------------------------------------------------------


def _www_authenticate(request: Request) -> str:
    """The ``WWW-Authenticate`` challenge on a /mcp 401, pointing at the
    protected-resource metadata so an OAuth-capable client (Claude Desktop /
    claude.ai) discovers the authorization flow. Per the MCP auth spec the 401
    MUST carry this ``resource_metadata`` pointer."""
    origin = request.app.state.settings.origin.rstrip("/")
    meta = f"{origin}/.well-known/oauth-protected-resource/mcp"
    return f'Bearer resource_metadata="{meta}"'


def mcp_only(request: Request) -> Principal:
    """Dual auth for the /mcp surface: EITHER the static ``token_mcp`` bearer
    (the ring / personal-server path, unchanged) OR a valid OAuth 2.1 access
    token bound to this ``/mcp`` resource (the Claude custom-connector path).

    Both resolve to the machine ``Principal("mcp", "mcp")`` so the tool layer is
    identical; for an OAuth call we additionally record which household user
    granted it (for logging). A missing/invalid bearer yields 401 with the
    discovery ``WWW-Authenticate`` header; a *valid* non-mcp static bearer
    (PWA/ring) is a 403 so the matrix stays unambiguous.
    """
    token = _extract_bearer(request)
    if token is None:
        raise HTTPException(
            status_code=401,
            detail="Authorization required",
            headers={"WWW-Authenticate": _www_authenticate(request)},
        )

    resolved = _authenticate_token(request.app.state.settings.tokens, token)
    if resolved is not None:
        if resolved.source == "mcp":
            return resolved
        raise HTTPException(
            status_code=403,
            detail="MCP endpoint requires the mcp token or an OAuth access token",
        )

    row = oauth.verify_access_token(request, token)
    if row is not None:
        request.state.oauth_user_id = row["user_id"]
        logger.info(
            "mcp oauth call: user_id=%s client=%s", row["user_id"], row["client_id"]
        )
        return Principal("mcp", "mcp")

    raise HTTPException(
        status_code=401,
        detail="Invalid token",
        headers={"WWW-Authenticate": _www_authenticate(request)},
    )


# --- tool definitions -----------------------------------------------------

TOOLS = [
    {
        "name": "add_items",
        "description": (
            "Add one or more items to the shopping list (batch). Runs the normal "
            "add path: dedup against active items, built-in icon lookup, background "
            "enrichment, and live-update publish. Returns each item with its id and "
            "whether it was newly created."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "note": {"type": "string"},
                        },
                        "required": ["name"],
                    },
                }
            },
            "required": ["items"],
        },
    },
    {
        "name": "get_shopping_list",
        "description": "Get the current shopping list: active items grouped by aisle, plus checked items.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "check_item",
        "description": (
            "Mark an active item as checked (bought). Matches by id or by fuzzy name "
            "(normalised substring) against active items. Returns what matched, or a "
            "clear not-found."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"name_or_id": {"type": "string"}},
            "required": ["name_or_id"],
        },
    },
    {
        "name": "uncheck_item",
        "description": (
            "Move a checked item back to active. Matches by id or fuzzy name against "
            "checked items. Returns what matched, or a clear not-found."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"name_or_id": {"type": "string"}},
            "required": ["name_or_id"],
        },
    },
    {
        "name": "remove_item",
        "description": (
            "Delete an item AND permanently forget that name's shortcut history "
            "— its learned icon, aisle and how often the household buys it. "
            "There is no undo. Use this only for a name that should stop being "
            "offered at all (a bad transcription, something bought by mistake). "
            "If the household has simply BOUGHT the thing, call check_item "
            "instead: tidying a list with remove_item silently erases staples "
            "from the history the shortcut tray is built on. Matches by id or "
            "fuzzy name against any item (active or checked). Returns what was "
            "removed, or a clear not-found."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"name_or_id": {"type": "string"}},
            "required": ["name_or_id"],
        },
    },
    {
        "name": "suggest_from_history",
        "description": (
            "Suggest items the household usually buys but that are not currently on "
            "the list, frecency-ranked. Optional hint filters by substring."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"hint": {"type": "string"}},
        },
    },
]


# --- tool implementations -------------------------------------------------


def _tool_result(structured: dict, *, is_error: bool = False, ttl: bool = False) -> dict:
    """Wrap a tool's structured payload in an MCP tools/call result. The text
    content mirrors the structured content for clients that only read text."""
    result: dict = {
        "content": [{"type": "text", "text": json.dumps(structured)}],
        "structuredContent": structured,
        "isError": is_error,
    }
    if ttl:
        result["_meta"] = {"ttlMs": LIST_TTL_MS}
    return result


def _grouped_list(request: Request) -> dict:
    """The /api/list shape: active items grouped by aisle in walk order."""
    repo = request.app.state.repo
    walk_order = request.app.state.settings.walk_order
    items = repo.list_items()
    grouped: dict[str, list] = {}
    for item in items["active"]:
        category = resolve_category(item["category"], walk_order)
        grouped.setdefault(category, []).append(item)
    group_order = walk_order if "Other" in walk_order else [*walk_order, "Other"]
    active = {cat: grouped[cat] for cat in group_order if cat in grouped}
    return {"active": active, "checked": items["checked"]}


def _find(repo, bucket: list[dict], name_or_id: str) -> dict | None:
    """Resolve an item within ``bucket`` by exact id or fuzzy normalised name.

    Fuzzy match: normalise both sides and accept when either normalised string
    contains the other, so "milk" matches "Organic Whole Milk" and vice versa.
    """
    for item in bucket:
        if item["id"] == name_or_id:
            return item
    known = repo.known_norms()
    query = normalise(name_or_id, known)
    for item in bucket:
        item_norm = normalise(item["name"], known)
        if query and (query in item_norm or item_norm in query):
            return item
    return None


def _tool_add_items(request, caller, args) -> dict:
    repo = request.app.state.repo
    bus = request.app.state.bus
    enricher = request.app.state.enricher
    added = []
    for entry in args.get("items", []):
        item, created = repo.add_item(
            id=None,
            name=entry["name"],
            note=entry.get("note"),
            source=caller.source,
            added_by=caller.name,
        )
        if created:
            bus.publish("item_added", item)
        else:
            bus.publish("item_updated", item)
        if enricher:
            enricher.schedule_on_add(item)
        added.append({**item, "created": created})
    return _tool_result({"added": added})


def _tool_get_shopping_list(request, caller, args) -> dict:
    return _tool_result(_grouped_list(request), ttl=True)


def _tool_check_item(request, caller, args) -> dict:
    repo = request.app.state.repo
    bus = request.app.state.bus
    match = _find(repo, repo.list_items()["active"], args["name_or_id"])
    if match is None:
        return _tool_result(
            {"matched": None, "message": f"No active item matching {args['name_or_id']!r}"},
            is_error=True,
        )
    item = repo.set_status(match["id"], "checked")
    bus.publish("item_updated", item)
    return _tool_result({"matched": item})


def _tool_uncheck_item(request, caller, args) -> dict:
    repo = request.app.state.repo
    bus = request.app.state.bus
    match = _find(repo, repo.list_items()["checked"], args["name_or_id"])
    if match is None:
        return _tool_result(
            {"matched": None, "message": f"No checked item matching {args['name_or_id']!r}"},
            is_error=True,
        )
    item = repo.set_status(match["id"], "active")
    bus.publish("item_updated", item)
    return _tool_result({"matched": item})


def _tool_remove_item(request, caller, args) -> dict:
    repo = request.app.state.repo
    bus = request.app.state.bus
    items = repo.list_items()
    match = _find(repo, items["active"] + items["checked"], args["name_or_id"])
    if match is None:
        return _tool_result(
            {"removed": None, "message": f"No item matching {args['name_or_id']!r}"},
            is_error=True,
        )
    repo.delete_item(match["id"])
    bus.publish("item_removed", {"id": match["id"]})
    return _tool_result({"removed": match})


def _tool_suggest_from_history(request, caller, args) -> dict:
    repo = request.app.state.repo
    active = repo.list_items()["active"]
    known = repo.known_norms()
    active_norms = {normalise(i["name"], known) for i in active}
    hint = (args.get("hint") or "").lower().strip()
    suggestions = []
    for entry in repo.catalog_top(200):
        if entry["name_norm"] in active_norms:
            continue
        if hint and hint not in entry["name_norm"] and hint not in entry["display_name"].lower():
            continue
        suggestions.append({field: entry.get(field) for field in _CATALOG_FIELDS})
        if len(suggestions) >= 12:
            break
    return _tool_result({"suggestions": suggestions}, ttl=True)


_DISPATCH = {
    "add_items": _tool_add_items,
    "get_shopping_list": _tool_get_shopping_list,
    "check_item": _tool_check_item,
    "uncheck_item": _tool_uncheck_item,
    "remove_item": _tool_remove_item,
    "suggest_from_history": _tool_suggest_from_history,
}


# --- JSON-RPC envelope ----------------------------------------------------


def _ok(id, result) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id, "result": result})


def _err(id, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}})


@router.post("/mcp")
async def mcp_endpoint(request: Request, caller: Principal = Depends(mcp_only)):
    try:
        message = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _err(None, -32700, "Parse error")
    if not isinstance(message, dict):
        # Batching is out of scope for the stateless surface.
        return _err(None, -32600, "Invalid Request")

    method = message.get("method")
    msg_id = message.get("id")
    is_notification = "id" not in message

    # Notifications (no id) — e.g. notifications/initialized — get 202, no body.
    if is_notification:
        return Response(status_code=202)

    if method == "initialize":
        params = message.get("params") or {}
        return _ok(
            msg_id,
            {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": _server_info(
                    request.app.state.settings.origin.rstrip("/")
                ),
            },
        )

    if method == "ping":
        return _ok(msg_id, {})

    if method == "tools/list":
        return _ok(msg_id, {"tools": TOOLS})

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        handler = _DISPATCH.get(name)
        if handler is None:
            return _err(msg_id, -32602, f"Unknown tool: {name}")
        args = params.get("arguments") or {}
        try:
            return _ok(msg_id, handler(request, caller, args))
        except KeyError as exc:
            # A required argument (e.g. an item's ``name``) was absent. Surface
            # it as an invalid-params error rather than a 500 — the schema
            # documents the requirement, so this is a client mistake.
            return _err(msg_id, -32602, f"Missing required argument: {exc}")

    return _err(msg_id, -32601, f"Method not found: {method}")
