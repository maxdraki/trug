from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from trug.auth import Principal, ring_only

router = APIRouter(prefix="/api")


class Capture(BaseModel):
    text: str


async def _text_from_multipart(request: Request) -> str:
    """Pull the ring's `transcription` field out of a multipart/form-data body.

    The Pebble ring POSTs `multipart/form-data` (never JSON): `transcription`
    (present when transcription is enabled/succeeded), `recordedAt`, `client`,
    and optionally an `audio` (audio/mp4) part. We only want the transcription.

    `async with request.form()` streams the parse via python-multipart and, on
    exit, closes any uploaded file — an `audio` part lands in a SpooledTemporary
    File that spills to disk past a small threshold rather than being buffered
    whole in memory, so an audio-only POST is read-and-discarded cheaply. We
    never read the audio bytes.
    """
    async with request.form() as form:
        value = form.get("transcription")
    # A form field is a str; an UploadFile (or missing field) is not a
    # transcription and reads as empty.
    return value if isinstance(value, str) else ""


@router.post("/capture")
async def capture(
    request: Request,
    user: Principal = Depends(ring_only),
):
    content_type = request.headers.get("content-type", "")
    # The ring sends multipart/form-data; also accept url-encoded form bodies so
    # a simpler client shape still works. Anything else is treated as JSON.
    if content_type.startswith(("multipart/form-data", "application/x-www-form-urlencoded")):
        text = await _text_from_multipart(request)
        # Audio-only (missing/empty transcription) is a success, never a 4xx:
        # the ring ignores response bodies but retries on failure, so a
        # transcription-less clip must not look like an error.
        if not text.strip():
            return {"added": [], "note": "no transcription"}
    else:
        # JSON {text} — the existing app/PWA path, unchanged.
        text = Capture(**await request.json()).text

    repo = request.app.state.repo
    bus = request.app.state.bus
    enricher = request.app.state.enricher

    parsed = await enricher.parse(text)

    added: list[dict] = []
    for entry in parsed:
        item, created = repo.add_item(
            id=None,
            name=entry["name"],
            note=entry.get("note"),
            source="ring",
            added_by=None,
        )
        if created:
            bus.publish("item_added", item)
        else:
            bus.publish("item_updated", item)
        if enricher:
            enricher.schedule_on_add(item)
        added.append(item)

    return {"added": added}
