from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from trug.auth import (
    Principal,
    _authenticate_session,
    _authenticate_token,
    _extract_bearer,
)
from trug.bus import EventBus

router = APIRouter(prefix="/api")

_PING = ": ping\n\n"
_TIMEOUT = 25


def events_principal(request: Request) -> Principal:
    """Authenticate an SSE client via bearer header or ``?token=`` query.

    Browsers authenticate same-origin ``EventSource`` via the session cookie
    (sent automatically). Machine clients that cannot set headers may still
    pass a bearer as the ``?token=`` query parameter.
    """
    # Human path: the session cookie (EventSource sends it same-origin).
    session_principal = _authenticate_session(request)
    if session_principal is not None:
        return session_principal

    token = _extract_bearer(request) or request.query_params.get("token")
    if token is None:
        raise HTTPException(
            status_code=401,
            detail="Missing token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    principal = _authenticate_token(request.app.state.settings.tokens, token)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


async def event_stream(bus: EventBus):
    queue = bus.subscribe()
    try:
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), _TIMEOUT)
            except TimeoutError:
                yield _PING
                continue
            payload = json.dumps(message["data"])
            yield f"event: {message['event']}\ndata: {payload}\n\n"
    finally:
        bus.unsubscribe(queue)


@router.get("/events")
def events(request: Request):
    events_principal(request)
    bus = request.app.state.bus
    return StreamingResponse(
        event_stream(bus),
        media_type="text/event-stream",
    )
