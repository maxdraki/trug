from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from trug.auth import Principal, principal

router = APIRouter(prefix="/api")

_FIELDS = ("name_norm", "display_name", "icon", "category", "times_added")


def _shape(entry: dict) -> dict:
    return {field: entry.get(field) for field in _FIELDS}


@router.get("/catalog")
def catalog_search(
    request: Request,
    q: str = "",
    user: Principal = Depends(principal),
):
    repo = request.app.state.repo
    return [_shape(e) for e in repo.catalog_search(q)]


@router.get("/catalog/top")
def catalog_top(
    request: Request,
    n: int = 24,
    user: Principal = Depends(principal),
):
    repo = request.app.state.repo
    return [_shape(e) for e in repo.catalog_top(n)]
