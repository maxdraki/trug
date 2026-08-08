"""HTTP surface for Trug's in-process OAuth 2.1 + CIMD authorization server.

Discovery (RFC 8414 / RFC 9728 well-knowns), the authorization endpoint (with a
server-rendered consent page gated on the human's passkey session), the token
endpoint (PKCE code exchange + rotating refresh), and a minimal deprecated DCR
registration endpoint as a fallback for clients that don't do CIMD. See
``trug/oauth.py`` for the pure logic and the spec notes.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import quote, urlencode, urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from trug import oauth
from trug.auth import SESSION_COOKIE, _authenticate_session
from trug.ratelimit import dcr_rate_limit, token_rate_limit

logger = logging.getLogger("trug.oauth")

router = APIRouter()

# The flow params carried through the consent page as hidden form fields.
_FLOW_PARAMS = (
    "client_id",
    "redirect_uri",
    "state",
    "code_challenge",
    "code_challenge_method",
    "scope",
    "resource",
)


def _no_store(payload: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status,
                        headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


def _oauth_error(error: str, description: str, status: int = 400) -> JSONResponse:
    return _no_store({"error": error, "error_description": description}, status)


# --- discovery -------------------------------------------------------------


def _origin(request: Request) -> str:
    return request.app.state.settings.origin


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource(request: Request):
    return JSONResponse(oauth.protected_resource_metadata(_origin(request)))


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/mcp")
def authorization_server(request: Request):
    return JSONResponse(oauth.authorization_server_metadata(_origin(request)))


# --- authorize -------------------------------------------------------------


def _resolve_and_check_redirect(request: Request, client_id: str,
                                redirect_uri: str) -> dict:
    """Resolve the client (CIMD/known/DCR) and confirm the requested redirect
    is one it registered. Failures here MUST NOT redirect (the redirect target
    is exactly what we cannot yet trust) — they surface as a 400."""
    oauth_repo = request.app.state.oauth_repo
    client = oauth.resolve_client(client_id, oauth_repo)
    if client is None:
        raise HTTPException(status_code=400, detail="Unknown or unresolvable client_id")
    if not redirect_uri or not oauth.redirect_uri_allowed(
        client["redirect_uris"], redirect_uri
    ):
        raise HTTPException(status_code=400, detail="redirect_uri not registered for client")
    return client


@router.get("/oauth/authorize")
def authorize(request: Request):
    params = request.query_params
    client_id = params.get("client_id", "")
    redirect_uri = params.get("redirect_uri", "")

    # Human gate FIRST, before any client resolution: resolving an unknown
    # client_id can trigger a CIMD network fetch + cache write (see
    # _resolve_and_check_redirect / oauth.resolve_client). An unauthenticated
    # caller must not be able to induce outbound fetches to arbitrary public
    # HTTPS URLs or grow the cache just by hitting this endpoint. Without a
    # session, bounce to the PWA (which handles sign-in) carrying a ``next``
    # back to this exact authorize URL, so approval resumes after sign-in —
    # no resolution, no fetch, no error-redirect leakage.
    session = _authenticate_session(request)
    if session is None:
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(url=f"/?next={quote(target, safe='')}", status_code=302)

    # Validate client + redirect next; these errors cannot be delivered to the
    # redirect target, so they are plain 400s.
    client = _resolve_and_check_redirect(request, client_id, redirect_uri)

    if params.get("response_type") != "code":
        return _redirect_error(redirect_uri, params, "unsupported_response_type",
                               "only response_type=code is supported")
    challenge = params.get("code_challenge")
    if not challenge or params.get("code_challenge_method", "S256") != "S256":
        return _redirect_error(redirect_uri, params, "invalid_request",
                               "S256 PKCE code_challenge is required")

    hidden = {k: params.get(k, "") for k in _FLOW_PARAMS if params.get(k) is not None}
    redirect_host = urlparse(redirect_uri).hostname or redirect_uri
    page = oauth.consent_page(
        client.get("client_name"), session.name, "/oauth/authorize",
        hidden, redirect_host,
    )
    return _html_no_frame(page)


def _html_no_frame(page: str, status: int = 200) -> HTMLResponse:
    """Server-rendered auth HTML must never be framed: it's a consent/decision
    surface, so a clickjacking iframe could trick the signed-in user into
    approving a malicious client."""
    return HTMLResponse(page, status_code=status, headers={
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": "frame-ancestors 'none'",
    })


def _redirect_error(redirect_uri: str, params, error: str, description: str) -> RedirectResponse:
    query = {"error": error, "error_description": description}
    if params.get("state"):
        query["state"] = params["state"]
    return RedirectResponse(url=_append_query(redirect_uri, query), status_code=302)


def _append_query(url: str, query: dict) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{urlencode(query)}"


@router.post("/oauth/authorize")
async def authorize_decision(request: Request):
    form = await request.form()

    # CSRF: a state-changing POST carrying the session cookie must come from our
    # own origin (mirrors the /auth csrf_guard). The consent form is same-origin.
    cookie_origin = request.headers.get("origin")
    if SESSION_COOKIE in request.cookies and cookie_origin not in (
        None, request.app.state.settings.origin
    ):
        raise HTTPException(status_code=403, detail="Bad origin")

    session = _authenticate_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Sign-in required")

    client_id = form.get("client_id", "")
    redirect_uri = form.get("redirect_uri", "")
    # Re-validate client + redirect on the POST (the hidden fields are attacker-
    # influenceable); the return value is unused here beyond that guard.
    _resolve_and_check_redirect(request, client_id, redirect_uri)

    if form.get("decision") != "approve":
        return _redirect_error(redirect_uri, form, "access_denied", "user denied consent")

    challenge = form.get("code_challenge")
    if not challenge:
        return _redirect_error(redirect_uri, form, "invalid_request",
                               "S256 PKCE code_challenge is required")

    auth_repo = request.app.state.auth_repo
    oauth_repo = request.app.state.oauth_repo
    user = auth_repo.get_user_by_name(session.name)
    if user is None:
        raise HTTPException(status_code=401, detail="Unknown user")

    code = oauth.new_token()
    oauth_repo.create_code(
        oauth.token_hash(code),
        client_id=client_id,
        user_id=user["id"],
        redirect_uri=redirect_uri,
        code_challenge=challenge,
        code_challenge_method=form.get("code_challenge_method") or "S256",
        resource=form.get("resource") or None,
        scope=form.get("scope") or oauth.DEFAULT_SCOPE,
        ttl_seconds=oauth.CODE_TTL,
    )
    logger.info(
        "oauth code issued: client=%s user=%s resource=%s",
        client_id, session.name, form.get("resource"),
    )

    query = {"code": code, "iss": request.app.state.settings.origin}
    if form.get("state"):
        query["state"] = form["state"]
    return RedirectResponse(url=_append_query(redirect_uri, query), status_code=302)


# --- token -----------------------------------------------------------------


@router.post("/oauth/token", dependencies=[token_rate_limit])
async def token(request: Request):
    form = await request.form()
    grant_type = form.get("grant_type")
    if grant_type == "authorization_code":
        return _grant_authorization_code(request, form)
    if grant_type == "refresh_token":
        return _grant_refresh_token(request, form)
    return _oauth_error("unsupported_grant_type", f"unsupported grant_type: {grant_type}")


def _issue_tokens(request: Request, client_id: str, user_id: str,
                  resource: str | None, scope: str | None) -> JSONResponse:
    oauth_repo = request.app.state.oauth_repo
    access = oauth.new_token()
    refresh = oauth.new_token()
    oauth_repo.create_access_token(
        oauth.token_hash(access), client_id, user_id, resource, scope, oauth.ACCESS_TTL
    )
    oauth_repo.create_refresh_token(
        oauth.token_hash(refresh), client_id, user_id, resource, scope, oauth.REFRESH_TTL
    )
    body = {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": oauth.ACCESS_TTL,
        "refresh_token": refresh,
        "scope": scope or oauth.DEFAULT_SCOPE,
    }
    return _no_store(body)


def _grant_authorization_code(request: Request, form) -> JSONResponse:
    code = form.get("code")
    if not code:
        return _oauth_error("invalid_request", "missing code")
    oauth_repo = request.app.state.oauth_repo
    row = oauth_repo.consume_code(oauth.token_hash(code))
    if row is None:
        return _oauth_error("invalid_grant", "code is invalid, expired, or already used")

    # client_id must match the code's; redirect_uri must match exactly.
    if form.get("client_id") and form.get("client_id") != row["client_id"]:
        return _oauth_error("invalid_grant", "client_id mismatch")
    if form.get("redirect_uri") != row["redirect_uri"]:
        return _oauth_error("invalid_grant", "redirect_uri mismatch")

    # PKCE: verify the code_verifier against the stored S256 challenge.
    if not oauth.verify_pkce(
        form.get("code_verifier") or "", row["code_challenge"], row["code_challenge_method"]
    ):
        return _oauth_error("invalid_grant", "PKCE verification failed")

    # RFC 8707 resource indicator: if the client requested a resource at the
    # token endpoint it must match the one bound at authorization time. Bind the
    # issued access token to that resource (the /mcp audience).
    requested_resource = form.get("resource")
    if requested_resource and row["resource"] and requested_resource != row["resource"]:
        return _oauth_error("invalid_target", "resource does not match the authorized resource")
    resource = row["resource"] or requested_resource

    return _issue_tokens(request, row["client_id"], row["user_id"], resource, row["scope"])


def _grant_refresh_token(request: Request, form) -> JSONResponse:
    presented = form.get("refresh_token")
    if not presented:
        return _oauth_error("invalid_request", "missing refresh_token")
    oauth_repo = request.app.state.oauth_repo
    row = oauth_repo.rotate_refresh_token(oauth.token_hash(presented))
    if row is None:
        # RFC 6749: an invalid/expired/revoked refresh token is invalid_grant.
        return _oauth_error("invalid_grant", "refresh_token is invalid, expired, or revoked")
    if form.get("client_id") and form.get("client_id") != row["client_id"]:
        return _oauth_error("invalid_grant", "client_id mismatch")
    return _issue_tokens(request, row["client_id"], row["user_id"], row["resource"], row["scope"])


# --- DCR (deprecated fallback) --------------------------------------------

# Bounds on the unauthenticated DCR write. Without them a curl loop could fill
# ./data with fresh client rows (each with an arbitrarily large redirect_uris
# blob) until every DB write fails. Deliberately generous — a real client
# registers a handful of short redirect URIs — but finite.
_MAX_REDIRECT_URIS = 10
_MAX_REDIRECT_URI_LEN = 512
_MAX_CLIENT_NAME_LEN = 256


def _is_absolute_uri(value: str) -> bool:
    """True when ``value`` is an absolute http(s) redirect URI (scheme + netloc).

    Only http/https are accepted. Custom/native-app schemes (and script schemes
    like javascript:/data:/vbscript:) are intentionally dropped for this
    deprecated DCR fallback — an auth server must never store or reflect a
    script-scheme redirect target, and CIMD (https + loopback) is the real path.

    ``urlparse`` itself raises ValueError ("Invalid IPv6 URL") on a crafted value
    like ``https://[::1`` — and this endpoint is unauthenticated — so the parse
    is guarded: an unparseable value is simply not a valid redirect_uri, never a
    500 (mirrors the urlsplit guard in app.py)."""
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


@router.post("/oauth/register", dependencies=[dcr_rate_limit])
async def register(request: Request):
    """Minimal RFC 7591 Dynamic Client Registration. Deprecated per the
    2026-07-28 spec (CIMD is preferred) but retained as the fallback Claude uses
    when it can't select CIMD. Public clients only (``token_endpoint_auth_method
    = none``); no client secret issued.

    Unauthenticated, so it is rate-limited (``dcr_rate_limit``, applied as a
    dependency so it runs before any body work) and the request is bounded: a
    capped number of redirect_uris, each a length-limited absolute URI, and a
    length-capped client_name — so it cannot be abused to bloat the DB."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return _oauth_error("invalid_client_metadata", "body must be JSON")
    redirect_uris = body.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return _oauth_error("invalid_redirect_uri", "redirect_uris is required")
    if len(redirect_uris) > _MAX_REDIRECT_URIS:
        return _oauth_error(
            "invalid_redirect_uri",
            f"at most {_MAX_REDIRECT_URIS} redirect_uris are allowed",
        )
    for uri in redirect_uris:
        if (
            not isinstance(uri, str)
            or len(uri) > _MAX_REDIRECT_URI_LEN
            or not _is_absolute_uri(uri)
        ):
            return _oauth_error(
                "invalid_redirect_uri",
                "each redirect_uri must be an absolute URI within the length limit",
            )

    client_name = body.get("client_name")
    if client_name is not None and (
        not isinstance(client_name, str) or len(client_name) > _MAX_CLIENT_NAME_LEN
    ):
        return _oauth_error(
            "invalid_client_metadata",
            f"client_name must be a string of at most {_MAX_CLIENT_NAME_LEN} characters",
        )

    oauth_repo = request.app.state.oauth_repo
    client_id = f"trug-client-{oauth.new_token()}"
    auth_method = body.get("token_endpoint_auth_method") or "none"
    oauth_repo.register_client(client_id, client_name, redirect_uris, auth_method)
    logger.info("oauth DCR client registered: id=%s name=%s", client_id, client_name)

    return _no_store(
        {
            "client_id": client_id,
            "redirect_uris": redirect_uris,
            "client_name": client_name,
            "token_endpoint_auth_method": auth_method,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
        status=201,
    )
