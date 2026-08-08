from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from trug.auth import Principal, principal
from trug.categories import resolve_category

router = APIRouter(prefix="/api")


class AddItem(BaseModel):
    id: str | None = None
    name: str
    note: str | None = None


class PatchItem(BaseModel):
    status: Literal["active", "checked"] | None = None
    note: str | None = None
    category: str | None = None
    # Reorder position; must be a finite number (no inf/nan).
    sort_key: float | None = Field(default=None, allow_inf_nan=False)


@router.get("/list")
def get_list(request: Request, user: Principal = Depends(principal)):
    repo = request.app.state.repo
    walk_order = request.app.state.settings.walk_order
    items = repo.list_items()

    grouped: dict[str, list] = {}
    for item in items["active"]:
        category = resolve_category(item["category"], walk_order)
        grouped.setdefault(category, []).append(item)

    # resolve_category falls back to "Other", so the walk order used for
    # grouping must always include it even if a custom config omits it.
    group_order = walk_order if "Other" in walk_order else [*walk_order, "Other"]
    active = {cat: grouped[cat] for cat in group_order if cat in grouped}
    return {"active": active, "checked": items["checked"]}


@router.post("/items")
def add_item(
    body: AddItem,
    request: Request,
    response: Response,
    user: Principal = Depends(principal),
):
    repo = request.app.state.repo
    bus = request.app.state.bus
    item, created = repo.add_item(
        id=body.id,
        name=body.name,
        note=body.note,
        source=user.source,
        added_by=user.name,
    )
    response.headers["X-Created"] = "true" if created else "false"
    enricher = request.app.state.enricher
    if created:
        bus.publish("item_added", item)
    else:
        bus.publish("item_updated", item)
    if enricher:
        enricher.schedule_on_add(item)
    return item


@router.patch("/items/{item_id}")
def patch_item(
    item_id: str,
    body: PatchItem,
    request: Request,
    user: Principal = Depends(principal),
):
    repo = request.app.state.repo
    bus = request.app.state.bus
    walk_order = request.app.state.settings.walk_order

    fields = body.model_dump(exclude_unset=True)
    # status/sort_key must never be set to None; drop explicit nulls.
    if fields.get("status") is None:
        fields.pop("status", None)
    if "sort_key" in fields and fields["sort_key"] is None:
        fields.pop("sort_key")
    if "category" in fields:
        fields["category"] = resolve_category(fields["category"], walk_order)

    item = repo.update_item(item_id, **fields)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    if fields:
        bus.publish("item_updated", item)
    return item


@router.delete("/items/{item_id}", status_code=204)
def delete_item(
    item_id: str,
    request: Request,
    user: Principal = Depends(principal),
):
    repo = request.app.state.repo
    bus = request.app.state.bus
    if not repo.delete_item(item_id):
        raise HTTPException(status_code=404, detail="Item not found")
    bus.publish("item_removed", {"id": item_id})
    return Response(status_code=204)


@router.post("/list/clear-checked")
def clear_checked(request: Request, user: Principal = Depends(principal)):
    repo = request.app.state.repo
    bus = request.app.state.bus
    cleared = repo.clear_checked()
    bus.publish("list_cleared", {"cleared": cleared})
    return {"cleared": cleared}
