"""Passkey (WebAuthn) auth routes: invites, registration, login, sessions.

The four WebAuthn ceremony calls are wrapped behind module-level seam
functions (``webauthn_register_options``, ``verify_registration`` and their
login counterparts). Route-logic tests fake the seam; a smaller set of tests
drives the real py_webauthn library through the seam with its own test vectors.
"""

from __future__ import annotations

import json
import logging
import secrets

import uuid6
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url, options_to_json_dict
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from trug.auth import (
    SESSION_COOKIE,
    Principal,
    _authenticate_session,
    _extract_bearer,
    hash_token,
    principal,
)
from trug.ratelimit import (
    bootstrap_rate_limit,
    bootstrap_state_rate_limit,
    login_rate_limit,
    register_rate_limit,
)

router = APIRouter(prefix="/auth")

logger = logging.getLogger("trug.authn")

_CHALLENGE_TTL = 300  # 5 minutes
_INVITE_TTL = 24 * 3600  # 24 hours


def _cred_prefix(credential_id: str | None) -> str:
    """First few chars of a credential id for a log line — enough to correlate
    across attempts, not enough to be the credential itself. Never the challenge
    or any secret."""
    if not credential_id:
        return "?"
    return credential_id[:8]


# --- ceremony seam ----------------------------------------------------
# Thin wrappers around py_webauthn so route logic can be tested with the
# library faked, while dedicated tests exercise the real ceremonies.


def webauthn_register_options(settings, user, exclude):
    return generate_registration_options(
        rp_id=settings.rp_id,
        rp_name=settings.household,
        user_name=user["name"],
        user_id=user["id"].encode("utf-8"),
        user_display_name=user["display_name"],
        exclude_credentials=exclude,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            # Require user verification (PIN/biometric), not merely user
            # presence — a stolen unlocked authenticator alone can't register.
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )


def verify_registration(credential, expected_challenge, settings):
    return verify_registration_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=settings.rp_id,
        expected_origin=settings.origin,
        # Belt-and-braces alongside the REQUIRED option above: reject an
        # attestation that lacks the UV flag even if a client ignored it.
        require_user_verification=True,
    )


def webauthn_login_options(settings):
    return generate_authentication_options(
        rp_id=settings.rp_id,
        # Require user verification for login, mirroring registration.
        user_verification=UserVerificationRequirement.REQUIRED,
    )


def verify_authentication(credential, expected_challenge, public_key, sign_count, settings):
    return verify_authentication_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=settings.rp_id,
        expected_origin=settings.origin,
        credential_public_key=public_key,
        credential_current_sign_count=sign_count,
        # Belt-and-braces alongside the REQUIRED option above: reject an
        # assertion that lacks the UV flag even if a client ignored it.
        require_user_verification=True,
    )


# --- helpers ----------------------------------------------------------


def _session_only(request: Request) -> Principal:
    """Human-only guard: resolve the caller strictly via the ``trug_session``
    cookie, never a bearer. /auth/connections hands back the ring/MCP machine
    tokens, so a machine bearer must not be able to enumerate the others — only
    a human session may. Anything without a valid session cookie is 401."""
    resolved = _authenticate_session(request)
    if resolved is None:
        raise HTTPException(status_code=401, detail="Session required")
    return resolved


def csrf_guard(request: Request) -> None:
    """FastAPI dependency guarding every mutating /auth route. When a session
    cookie rides along, a cross-site page could trigger the request with the
    browser attaching the cookie. Requiring the Origin header to equal our own
    origin blocks that; machine (cookie-less) callers, which are not subject to
    ambient-credential CSRF, are unaffected."""
    if SESSION_COOKIE not in request.cookies:
        return
    origin = request.headers.get("origin")
    if origin != request.app.state.settings.origin:
        raise HTTPException(status_code=403, detail="Bad origin")


def _challenge_from_credential(credential: dict) -> str:
    try:
        client_data = base64url_to_bytes(credential["response"]["clientDataJSON"])
        return json.loads(client_data)["challenge"]
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Malformed credential") from exc


def _set_session_cookie(request: Request, response: Response, token: str) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.origin.lower().startswith("https"),
        samesite="lax",
        path="/",
    )


def _start_session(request: Request, response: Response, user_id: str) -> None:
    auth_repo = request.app.state.auth_repo
    settings = request.app.state.settings
    token = secrets.token_urlsafe(32)
    auth_repo.create_session(
        hash_token(token),
        user_id,
        settings.session_days,
        request.headers.get("user-agent"),
    )
    _set_session_cookie(request, response, token)


# --- request bodies ---------------------------------------------------


class InviteBody(BaseModel):
    name: str


class BootstrapBody(BaseModel):
    name: str


class BootstrapVerifyBody(BaseModel):
    name: str
    credential: dict


class RegisterOptionsBody(BaseModel):
    invite: str


class RegisterVerifyBody(BaseModel):
    invite: str
    credential: dict


class LoginVerifyBody(BaseModel):
    credential: dict


# --- routes -----------------------------------------------------------


# --- bootstrap (first-user claim) -------------------------------------
# A single bootstrap token claims user #1 on a fresh instance. The token is
# constant-time compared against settings.bootstrap_token and is only usable
# while the roster is empty; both claim endpoints 403 once any user exists, so
# the whole flow is strictly one-time and first-user-only.


def _require_bootstrap(request: Request) -> None:
    """Guard shared by both claim endpoints. 403s unless the roster is empty (or
    a recovery reopen is live) AND the caller presented the correct bootstrap
    token (Authorization: Bearer). The claimable check comes first so that, once
    claimed, the endpoint is a flat 403 that never even consults the token (no
    post-claim token oracle). The token itself is compared in constant time and
    never logged or returned.

    ``bootstrap_reopen_active`` is the ``recover --reset-bootstrap`` escape
    hatch: a full lockout leaves users on the roster (so user_count != 0) with no
    working credential, which would otherwise wedge this shut forever. The
    operator (who has host access — the recovery credential) sets the flag from
    the CLI to re-open the claim exactly once; a successful claim clears it."""
    auth_repo = request.app.state.auth_repo
    if auth_repo.user_count() != 0 and not auth_repo.bootstrap_reopen_active():
        raise HTTPException(status_code=403, detail="Bootstrap already claimed")
    expected = request.app.state.settings.bootstrap_token
    token = _extract_bearer(request)
    if (
        not token
        or not expected
        or not secrets.compare_digest(
            token.encode("utf-8", "surrogateescape"),
            expected.encode("utf-8", "surrogateescape"),
        )
    ):
        raise HTTPException(status_code=403, detail="Invalid bootstrap token")


@router.get("/bootstrap/state", dependencies=[bootstrap_state_rate_limit])
def bootstrap_state(request: Request):
    """Unauthenticated probe for the gate: is this instance still claimable?
    Reveals only whether the claim is open, never the token.

    This must agree with ``_require_bootstrap`` — including the recovery reopen
    — or the gate misreports. A locked-out operator who runs
    ``recover --reset-bootstrap`` still has users on the roster, so a bare
    ``user_count() == 0`` would say "claimed" while the claim endpoints were in
    fact open, hiding the first-run onboarding at the exact moment it is needed.
    """
    auth_repo = request.app.state.auth_repo
    claimable = auth_repo.user_count() == 0 or auth_repo.bootstrap_reopen_active()
    return {"claimable": claimable}


@router.post(
    "/bootstrap/claim/options",
    dependencies=[bootstrap_rate_limit, Depends(csrf_guard)],
)
def bootstrap_options(body: BootstrapBody, request: Request):
    _require_bootstrap(request)
    settings = request.app.state.settings
    auth_repo = request.app.state.auth_repo
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    # The user is NOT persisted here — that happens atomically in verify, keeping
    # user_count()==0 (and the flow open) until the ceremony actually completes.
    # A throwaway id backs the WebAuthn user handle; the stored user gets its own.
    ephemeral = {"id": str(uuid6.uuid7()), "name": name, "display_name": name}
    options = webauthn_register_options(settings, ephemeral, [])
    auth_repo.store_challenge(
        bytes_to_base64url(options.challenge), "bootstrap", _CHALLENGE_TTL
    )
    return options_to_json_dict(options)


@router.post(
    "/bootstrap/claim/verify",
    dependencies=[bootstrap_rate_limit, Depends(csrf_guard)],
)
def bootstrap_verify(
    body: BootstrapVerifyBody, request: Request, response: Response
):
    _require_bootstrap(request)
    settings = request.app.state.settings
    auth_repo = request.app.state.auth_repo
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")

    challenge = _challenge_from_credential(body.credential)
    if not auth_repo.consume_challenge(challenge, "bootstrap"):
        raise HTTPException(status_code=400, detail="Unknown or used challenge")

    try:
        verified = verify_registration(
            body.credential, base64url_to_bytes(challenge), settings
        )
    except Exception as exc:
        logger.warning(
            "bootstrap ceremony failed: name=%s cred=%s rp_id=%s origin=%s err=%s: %s",
            name,
            _cred_prefix(body.credential.get("id")),
            settings.rp_id,
            settings.origin,
            exc.__class__.__name__,
            exc,
        )
        raise HTTPException(status_code=400, detail="Registration failed") from exc

    # Atomic one-time claim: claim_first_user inserts the user, their credential
    # AND their session in a SINGLE transaction, only if the roster is still
    # empty. A concurrent second claim that got past the guard above finds a
    # non-empty table under the write lock and is refused (None), so exactly one
    # first user is ever made. Crucially, because the credential and session are
    # part of the same commit, a failure never leaves a committed user with no
    # credential — which would wedge bootstrap shut and brick the instance. We
    # generate the session token here and hand its hash in; the plaintext only
    # ever rides back to the client as the cookie.
    transports = body.credential.get("transports")
    token = secrets.token_urlsafe(32)
    # A live reopen flag switches claim_first_user into its non-destructive
    # recovery mode (attach to an existing same-named user, clear the flag).
    reopen = auth_repo.bootstrap_reopen_active()
    user = auth_repo.claim_first_user(
        name,
        bytes_to_base64url(verified.credential_id),
        verified.credential_public_key,
        verified.sign_count,
        ",".join(transports) if transports else None,
        hash_token(token),
        settings.session_days,
        request.headers.get("user-agent"),
        reopen=reopen,
    )
    if user is None:
        raise HTTPException(status_code=403, detail="Bootstrap already claimed")

    _set_session_cookie(request, response, token)
    return {"ok": True, "user": user["name"]}


# --- invite (any enrolled member) -------------------------------------


@router.post("/invite", dependencies=[Depends(csrf_guard)])
def create_invite(
    body: InviteBody,
    request: Request,
    user: Principal = Depends(_session_only),
):
    """Session-authed: any enrolled member (a valid session cookie implies one)
    may invite. No fixed roster — the name is any new name. Re-inviting a still
    pending user mints a fresh link; a name that is already an ENROLLED member
    is refused (they're already in)."""
    auth_repo = request.app.state.auth_repo
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    target = auth_repo.get_user_by_name(name)
    if target is None:
        target = auth_repo.create_user(name)
    elif auth_repo.credentials_for_user(target["id"]):
        raise HTTPException(status_code=409, detail="Already a member")
    token = secrets.token_urlsafe(32)
    auth_repo.create_invite(hash_token(token), target["id"], _INVITE_TTL)
    return {"invite": token, "name": name}


@router.post(
    "/register/options",
    dependencies=[register_rate_limit, Depends(csrf_guard)],
)
def register_options(body: RegisterOptionsBody, request: Request):
    auth_repo = request.app.state.auth_repo
    settings = request.app.state.settings
    token_hash = hash_token(body.invite)
    invite = auth_repo.get_valid_invite(token_hash)
    if invite is None:
        raise HTTPException(status_code=400, detail="Invalid or expired invite")
    target = auth_repo.get_user(invite["user_id"])
    exclude = [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(cred["credential_id"]))
        for cred in auth_repo.credentials_for_user(invite["user_id"])
    ]
    options = webauthn_register_options(settings, target, exclude)
    auth_repo.store_challenge(
        bytes_to_base64url(options.challenge),
        "register",
        _CHALLENGE_TTL,
        invite_token_hash=token_hash,
    )
    return options_to_json_dict(options)


@router.post(
    "/register/verify",
    dependencies=[register_rate_limit, Depends(csrf_guard)],
)
def register_verify(
    body: RegisterVerifyBody, request: Request, response: Response
):
    auth_repo = request.app.state.auth_repo
    settings = request.app.state.settings
    token_hash = hash_token(body.invite)
    invite = auth_repo.get_valid_invite(token_hash)
    if invite is None:
        raise HTTPException(status_code=400, detail="Invalid or expired invite")

    challenge = _challenge_from_credential(body.credential)
    if not auth_repo.consume_challenge(challenge, "register"):
        raise HTTPException(status_code=400, detail="Unknown or used challenge")

    try:
        verified = verify_registration(
            body.credential, base64url_to_bytes(challenge), settings
        )
    except Exception as exc:
        target = auth_repo.get_user(invite["user_id"])
        logger.warning(
            "registration ceremony failed: user=%s cred=%s rp_id=%s origin=%s err=%s: %s",
            target["name"] if target else invite["user_id"],
            _cred_prefix(body.credential.get("id")),
            settings.rp_id,
            settings.origin,
            exc.__class__.__name__,
            exc,
        )
        raise HTTPException(status_code=400, detail="Registration failed") from exc

    # Redeem the invite atomically before persisting anything: consume_invite
    # only returns True for the single caller that actually flips
    # used_at from NULL. Two verifies racing on the same invite (e.g. two
    # tabs, or a replayed request) can both reach this point after
    # passing get_valid_invite and verifying their own credential, but
    # only one may claim the invite — the loser must not add a
    # credential or start a session.
    if not auth_repo.consume_invite(token_hash):
        raise HTTPException(status_code=410, detail="Invite already used")

    transports = body.credential.get("transports")
    auth_repo.add_credential(
        invite["user_id"],
        bytes_to_base64url(verified.credential_id),
        verified.credential_public_key,
        verified.sign_count,
        ",".join(transports) if transports else None,
    )
    _start_session(request, response, invite["user_id"])
    user = auth_repo.get_user(invite["user_id"])
    return {"ok": True, "user": user["name"]}


@router.post(
    "/login/options",
    dependencies=[login_rate_limit, Depends(csrf_guard)],
)
def login_options(request: Request):
    auth_repo = request.app.state.auth_repo
    settings = request.app.state.settings
    options = webauthn_login_options(settings)
    auth_repo.store_challenge(
        bytes_to_base64url(options.challenge), "auth", _CHALLENGE_TTL
    )
    return options_to_json_dict(options)


@router.post(
    "/login/verify",
    dependencies=[login_rate_limit, Depends(csrf_guard)],
)
def login_verify(body: LoginVerifyBody, request: Request, response: Response):
    auth_repo = request.app.state.auth_repo
    settings = request.app.state.settings

    credential_id = body.credential.get("id")
    stored = auth_repo.get_credential(credential_id) if credential_id else None
    if stored is None:
        raise HTTPException(status_code=401, detail="Unknown credential")

    challenge = _challenge_from_credential(body.credential)
    if not auth_repo.consume_challenge(challenge, "auth"):
        raise HTTPException(status_code=401, detail="Unknown or used challenge")

    try:
        verified = verify_authentication(
            body.credential,
            base64url_to_bytes(challenge),
            bytes(stored["public_key"]),
            stored["sign_count"],
            settings,
        )
    except Exception as exc:
        if "sign count" in str(exc).lower():
            # Sign count failed to advance past the stored value: the signature
            # was otherwise valid, so this is the cloned-authenticator signal.
            # Log distinctly and loudly.
            logger.error(
                "sign-count regression (possible cloned authenticator): "
                "user=%s cred=%s stored_count=%s rp_id=%s origin=%s: %s",
                stored["user_id"],
                _cred_prefix(credential_id),
                stored["sign_count"],
                settings.rp_id,
                settings.origin,
                exc,
            )
        else:
            logger.warning(
                "authentication ceremony failed: user=%s cred=%s rp_id=%s origin=%s err=%s: %s",
                stored["user_id"],
                _cred_prefix(credential_id),
                settings.rp_id,
                settings.origin,
                exc.__class__.__name__,
                exc,
            )
        raise HTTPException(status_code=401, detail="Authentication failed") from exc

    auth_repo.update_sign_count(credential_id, verified.new_sign_count)
    _start_session(request, response, stored["user_id"])
    user = auth_repo.get_user(stored["user_id"])
    return {"ok": True, "user": user["name"]}


@router.post("/logout", dependencies=[Depends(csrf_guard)])
def logout(request: Request, response: Response, user: Principal = Depends(principal)):
    auth_repo = request.app.state.auth_repo
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        session = auth_repo.get_active_session(hash_token(token))
        if session is not None:
            auth_repo.revoke_session(session["id"], session["user_id"])
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/users")
def list_users(request: Request, user: Principal = Depends(_session_only)):
    """The dynamic household roster for the Members UI: each user's name,
    enrolled flag, created_at and credential count, plus ``me`` (the caller's
    name, so the UI can mark "you"). Session-only — humans manage the roster."""
    return {
        "users": request.app.state.auth_repo.list_users(),
        "me": user.name,
    }


@router.delete(
    "/members/{name}", status_code=204, dependencies=[Depends(csrf_guard)]
)
def remove_member(
    name: str,
    request: Request,
    response: Response,
    user: Principal = Depends(_session_only),
):
    """Remove a member (cascading credentials/sessions/invites). Removing a
    still-pending user is the "revoke invite" path. Guards: the last ENROLLED
    member cannot be removed (that would lock the household out — recovery is
    then DB/operator only); removing yourself is allowed but ends your session
    (the cookie is cleared here, and its server-side row was just deleted)."""
    auth_repo = request.app.state.auth_repo
    # The last-enrolled-member invariant is enforced INSIDE the delete
    # transaction (under the repo's write lock), not by a read-then-delete here:
    # two concurrent removals of the two last enrolled members would otherwise
    # both observe >1 enrolled and both delete, leaving zero enrolled and — with
    # any pending user still on the roster — bootstrap inert, i.e. a permanent
    # lockout. The storage layer is the source of truth; the UI's pre-disable of
    # the last-member remove is friendly UX on top of this.
    outcome = auth_repo.delete_member_unless_last_enrolled(name)
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="Unknown member")
    if outcome == "last_enrolled":
        raise HTTPException(
            status_code=409, detail="Cannot remove the last member"
        )
    if name == user.name:
        response.delete_cookie(SESSION_COOKIE, path="/")
    return Response(status_code=204)


@router.get("/connections")
def connections(request: Request, user: Principal = Depends(_session_only)):
    """Self-serve setup values for a human: the MCP endpoint + token and the
    ring webhook + token. URLs are TRUG_ORIGIN plus the fixed mount paths;
    tokens come straight from Settings (no DB read). Session-only via
    ``_session_only`` — a bearer cannot enumerate the machine tokens."""
    settings = request.app.state.settings
    origin = settings.origin.rstrip("/")
    return {
        "mcp_url": f"{origin}/mcp",
        "mcp_token": settings.tokens.get("mcp"),
        "webhook_url": f"{origin}/api/capture",
        "ring_token": settings.tokens.get("ring"),
    }


@router.get("/sessions")
def list_sessions(request: Request, user: Principal = Depends(principal)):
    auth_repo = request.app.state.auth_repo
    target = auth_repo.get_user_by_name(user.name)
    if target is None:
        return {"sessions": []}
    current = getattr(request.state, "session_id", None)
    sessions = [
        {
            "id": row["id"],
            "created_at": row["created_at"],
            "last_seen": row["last_seen"],
            "user_agent": row["user_agent"],
            "current": row["id"] == current,
        }
        for row in auth_repo.sessions_for_user(target["id"])
    ]
    return {"sessions": sessions}


@router.delete(
    "/sessions/{session_id}", status_code=204, dependencies=[Depends(csrf_guard)]
)
def revoke_session(
    session_id: str,
    request: Request,
    response: Response,
    user: Principal = Depends(principal),
):
    auth_repo = request.app.state.auth_repo
    target = auth_repo.get_user_by_name(user.name)
    if target is None or not auth_repo.revoke_session(session_id, target["id"]):
        raise HTTPException(status_code=404, detail="Session not found")
    return Response(status_code=204)
