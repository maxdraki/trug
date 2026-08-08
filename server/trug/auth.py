from __future__ import annotations

import hashlib
import secrets
from collections import namedtuple

from fastapi import Depends, HTTPException, Request

Principal = namedtuple("Principal", "name source")

SESSION_COOKIE = "trug_session"


def hash_token(token: str) -> str:
    """SHA-256 of a session/invite token. Only the hash is stored server-side,
    so a database leak never exposes a usable bearer/session token. Lookups are
    by the hash's full value (a 256-bit random preimage the attacker lacks),
    which is not a timing oracle the way a direct secret comparison would be."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

# The two fixed machine-token keys carry their own source; every other token
# key is a configured user and resolves to a human PWA principal of that name.
_MACHINE_SOURCES = {"ring": "ring", "mcp": "mcp"}


def _principal_for_key(key: str) -> Principal:
    return Principal(key, _MACHINE_SOURCES.get(key, "pwa"))


def _extract_bearer(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def _authenticate_token(tokens: dict[str, str], token: str) -> Principal | None:
    # Encode to bytes so non-ASCII tokens compare (and fail) cleanly rather
    # than raising TypeError inside compare_digest.
    token_bytes = token.encode("utf-8", "surrogateescape")
    for key, value in tokens.items():
        if secrets.compare_digest(value.encode("utf-8", "surrogateescape"), token_bytes):
            return _principal_for_key(key)
    return None


def _authenticate_session(request: Request) -> Principal | None:
    """Resolve a human via the ``trug_session`` cookie. Returns None when there
    is no cookie or it is invalid/expired/revoked; records the session id on
    ``request.state`` so /auth/sessions can flag the caller's current device."""
    cookies = getattr(request, "cookies", None)
    token = cookies.get(SESSION_COOKIE) if cookies else None
    if not token:
        return None
    auth_repo = getattr(request.app.state, "auth_repo", None)
    if auth_repo is None:
        return None
    session = auth_repo.get_active_session(hash_token(token))
    if session is None:
        return None
    # Rolling expiry: touch last_seen and extend when past the half-life.
    settings = request.app.state.settings
    auth_repo.touch_session(session["id"], settings.session_days)
    request.state.session_id = session["id"]
    return Principal(session["user_name"], "pwa")


def principal(request: Request) -> Principal:
    # Bearer (machine/legacy) takes precedence so ring/mcp/PWA-token paths keep
    # working exactly as before; the session cookie is the human fallback.
    token = _extract_bearer(request)
    if token is not None:
        tokens: dict[str, str] = request.app.state.settings.tokens
        resolved = _authenticate_token(tokens, token)
        if resolved is None:
            raise HTTPException(status_code=401, detail="Invalid token", headers={"WWW-Authenticate": "Bearer"})
        return resolved

    session_principal = _authenticate_session(request)
    if session_principal is not None:
        return session_principal

    raise HTTPException(status_code=401, detail="Missing bearer token", headers={"WWW-Authenticate": "Bearer"})


def ring_only(principal: Principal = Depends(principal)) -> Principal:
    if principal.source != "ring":
        raise HTTPException(status_code=403, detail="Ring-only endpoint")
    return principal
