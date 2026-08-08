import asyncio
from types import SimpleNamespace

import anyio
import pytest
from fastapi import HTTPException

from trug.bus import EventBus
from trug.routes.events import event_stream, events_principal


def test_event_stream_formats_sse():
    async def run():
        bus = EventBus()
        gen = event_stream(bus)
        # The generator subscribes on its first step; run it up to the
        # ``queue.get()`` await before publishing so the event is delivered.
        step = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0)
        bus.publish("item_added", {"id": "x"})
        chunk = await step
        assert chunk == 'event: item_added\ndata: {"id": "x"}\n\n'

    anyio.run(run)


def _fake_request(headers=None, query=None):
    # Mirror the machine tokens that actually exist in production Settings
    # (ring + mcp); the retired {alice, bob} names asserted a fiction, exercising
    # a path that would never accept in the real app.
    tokens = {"ring": "tok-ring", "mcp": "tok-mcp"}
    settings = SimpleNamespace(tokens=tokens)
    app = SimpleNamespace(state=SimpleNamespace(settings=settings))
    return SimpleNamespace(headers=headers or {}, query_params=query or {}, app=app)


# TestClient buffers the whole response, so it cannot consume an infinite SSE
# stream; exercise the query-token auth path directly instead.
def test_events_query_token_authenticates():
    principal = events_principal(_fake_request(query={"token": "tok-ring"}))
    assert principal.name == "ring"


def test_events_bad_query_token_rejected():
    with pytest.raises(HTTPException) as exc:
        events_principal(_fake_request(query={"token": "nope"}))
    assert exc.value.status_code == 401


def test_events_missing_auth_rejected(client):
    # Drop the fixture's session cookie so this is a genuinely anonymous call.
    # Auth is checked before streaming begins, so this returns without hanging.
    client.cookies.clear()
    assert client.get("/api/events").status_code == 401


def test_events_header_bearer_authenticates():
    principal = events_principal(
        _fake_request(headers={"Authorization": "Bearer tok-mcp"})
    )
    assert principal.name == "mcp"
