from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

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


@router.delete("/catalog", status_code=204)
def forget_catalog(
    request: Request,
    name_norm: str,
    user: Principal = Depends(principal),
):
    """Stop offering a shortcut. ``name_norm`` is a catalogue key exactly as the
    tray was given it — matched verbatim rather than re-run through
    ``normalise``, because normalising here could fold the key onto a
    NEIGHBOURING catalogue row ("lemon" onto an established "lemons") and forget
    the wrong shortcut. A key that does not match a row 404s, which is the
    honest answer for a stale tray on a second device.

    The key is a QUERY parameter, not a path segment, because ``normalise``
    preserves "/" — "Salt / Pepper" is catalogued as "salt / pepper" — and
    Starlette percent-decodes before routing, so such a key splits the path and
    can never be addressed, whatever the client encodes (``{name_norm:path}``
    does not help: the leading segment still splits). In the query string every
    byte a name can hold survives verbatim.
    """
    repo = request.app.state.repo
    if not repo.forget_catalog(name_norm):
        raise HTTPException(status_code=404, detail="Shortcut not found")
    # The tray refetches on this rather than tracking catalogue state itself.
    request.app.state.bus.publish("catalog_forgotten", {"name_norm": name_norm})
    return Response(status_code=204)


@router.get("/catalog/top")
def catalog_top(
    request: Request,
    n: int = 24,
    user: Principal = Depends(principal),
):
    repo = request.app.state.repo
    return [_shape(e) for e in repo.catalog_top(n)]
