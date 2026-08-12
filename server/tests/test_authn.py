"""Passkey (WebAuthn) auth-layer tests.

Coverage is split into two kinds, called out per test:

* REAL — drives the actual py_webauthn library through the ceremony seam using
  the library's own published test vectors (registration + EC2 authentication).
  These prove attestation/assertion verification, the cloned-authenticator
  sign-count guard, and origin validation for real.
* SEAMED — fakes the ceremony seam (``verify_registration`` /
  ``verify_authentication``) to exercise route logic, invites, sessions and
  cookies deterministically without a soft authenticator.
"""

import base64
import json
import sqlite3
import threading
import types
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from webauthn import verify_authentication_response

from trug.app import create_app
from trug.auth import SESSION_COOKIE, hash_token
from trug.auth_repo import AuthRepository
from trug.config import Settings
from trug.routes import authn

ORIGIN = "http://localhost:8000"


def make_auth_client(origin=ORIGIN, rp_id="localhost", env=None, seed=("alice", "bob")):
    """Build a test client. The roster is dynamic now (no TRUG_USERS), so the
    household is seeded directly into the repo for tests that need existing
    users; pass ``seed=()`` for a pristine zero-user instance (bootstrap tests)."""
    s = Settings.load(
        {
            "TRUG_TOKEN_RING": "tok-ring",
            "TRUG_TOKEN_MCP": "tok-mcp",
            "TRUG_BOOTSTRAP_TOKEN": "boot-secret",
            "TRUG_DB_PATH": ":memory:",
            "TRUG_RP_ID": rp_id,
            "TRUG_ORIGIN": origin,
            **(env or {}),
        },
        None,
    )
    app = create_app(s)
    if seed:
        app.state.auth_repo.seed_users(list(seed))
    return TestClient(app), app


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_credential(challenge, cred_id="cred1", origin=ORIGIN):
    """A minimal WebAuthn-shaped credential whose clientDataJSON carries the
    given challenge — enough for the route to extract/validate it."""
    cdj = json.dumps(
        {"type": "webauthn.get", "challenge": challenge, "origin": origin}
    ).encode()
    return {
        "id": cred_id,
        "rawId": cred_id,
        "response": {"clientDataJSON": _b64url(cdj)},
        "type": "public-key",
        "transports": ["internal"],
    }


class FakeReg:
    def __init__(self, credential_id, credential_public_key, sign_count):
        self.credential_id = credential_id
        self.credential_public_key = credential_public_key
        self.sign_count = sign_count


class FakeAuth:
    def __init__(self, credential_id, new_sign_count):
        self.credential_id = credential_id
        self.new_sign_count = new_sign_count


def fake_register(**expected):
    def _f(credential, expected_challenge, settings):
        return FakeReg(b"\x01\x02\x03\x04", b"PUBLICKEYBYTES", 5)

    return _f


# ---------------------------------------------------------------------------
# SEAMED: invite lifecycle
# ---------------------------------------------------------------------------


def test_invite_requires_session_not_bearer(monkeypatch):
    """SEAMED — invite is session-authed: machine/anonymous bearers cannot mint
    invites (401), only an enrolled member's session cookie."""
    c, _ = make_auth_client()
    for tok in ("tok-ring", "tok-mcp"):
        r = c.post("/auth/invite", json={"name": "guest"},
                   headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401, tok
    # Anonymous is also 401.
    assert c.post("/auth/invite", json={"name": "guest"}).status_code == 401


def test_invite_minted_by_enrolled_member(monkeypatch):
    """SEAMED — an enrolled member (holds a session cookie) can invite any new
    name; the pending user is created on the fly."""
    c, app = make_auth_client()
    _register(c, app, monkeypatch)  # jar now holds alice's session cookie
    r = c.post("/auth/invite", json={"name": "guest"}, headers={"origin": ORIGIN})
    assert r.status_code == 200
    assert r.json()["invite"]
    assert r.json()["name"] == "guest"
    # The new name now exists as a pending (unenrolled) user.
    assert app.state.auth_repo.get_user_by_name("guest") is not None


def test_invite_new_name_creates_pending_user(monkeypatch):
    """SEAMED — inviting a brand-new name (not on any fixed list) works."""
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    r = c.post("/auth/invite", json={"name": "brand-new"},
               headers={"origin": ORIGIN})
    assert r.status_code == 200
    members = {m["name"] for m in app.state.auth_repo.list_users()}
    assert "brand-new" in members


def test_invite_rejects_already_enrolled_member(monkeypatch):
    """SEAMED — you can't invite someone who is already an enrolled member."""
    c, app = make_auth_client()
    _register(c, app, monkeypatch)  # alice is now enrolled
    r = c.post("/auth/invite", json={"name": "alice"}, headers={"origin": ORIGIN})
    assert r.status_code == 409


def test_invite_reinvites_pending_user(monkeypatch):
    """SEAMED — re-inviting a still-pending user mints a fresh link (allowed)."""
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    r1 = c.post("/auth/invite", json={"name": "bob"}, headers={"origin": ORIGIN})
    r2 = c.post("/auth/invite", json={"name": "bob"}, headers={"origin": ORIGIN})
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["invite"] != r2.json()["invite"]


def test_invite_single_use(monkeypatch):
    """SEAMED — an invite is consumed on successful registration and cannot be
    reused."""
    c, app = make_auth_client()
    repo = app.state.auth_repo
    max_id = repo.get_user_by_name("alice")["id"]
    repo.create_invite(hash_token("inv1"), max_id, 3600)
    repo.store_challenge("Q0hBTA", "register", 300)
    monkeypatch.setattr(authn, "verify_registration", fake_register())

    cred = make_credential("Q0hBTA")
    r = c.post("/auth/register/verify", json={"invite": "inv1", "credential": cred})
    assert r.status_code == 200
    # Invite now consumed → a second register/options rejects it. (The client
    # now holds a session cookie, so the same-origin header is required.)
    r2 = c.post("/auth/register/options", json={"invite": "inv1"},
                headers={"origin": ORIGIN})
    assert r2.status_code == 400


def test_invite_race_exactly_one_registration_succeeds(monkeypatch):
    """SEAMED — two register/verify calls staged against the same invite,
    genuinely concurrent (run on two threads, synchronized with a barrier so
    both pass get_valid_invite and consume their own distinct challenge
    before either reaches invite redemption). This is the actual race: two
    requests that both observed the invite as still valid, racing to
    redeem it. redeem_invite_with_credential's atomic UPDATE ... WHERE used_at
    IS NULL means only one caller can flip it, and the credential insert rides
    in that same transaction — so exactly one of the two verifies succeeds
    (200) and the other is told the invite is already used (410), with only one
    credential row ever written."""
    c, app = make_auth_client()
    repo = app.state.auth_repo
    max_id = repo.get_user_by_name("alice")["id"]
    repo.create_invite(hash_token("inv-race"), max_id, 3600)
    repo.store_challenge("Y2hhbGxlbmdlLWEtYnl0ZXM", "register", 300)
    repo.store_challenge("Y2hhbGxlbmdlLWItYnl0ZXM", "register", 300)

    barrier = threading.Barrier(2, timeout=5)

    def blocking_fake_register(credential, expected_challenge, settings):
        barrier.wait()  # hold both threads here until both have a claim
        return FakeReg(credential["id"].encode(), b"PUBLICKEYBYTES", 5)

    monkeypatch.setattr(authn, "verify_registration", blocking_fake_register)

    cred_a = make_credential("Y2hhbGxlbmdlLWEtYnl0ZXM", cred_id="credA")
    cred_b = make_credential("Y2hhbGxlbmdlLWItYnl0ZXM", cred_id="credB")

    results: dict[str, object] = {}
    # A thread that dies used to leave `results` short, so the assertions below
    # failed with a bare KeyError and the real exception was buried in stderr.
    # Capture each thread's own failure and re-raise it here instead.
    failures: list[BaseException] = []

    def call(key, credential, headers):
        try:
            results[key] = c.post(
                "/auth/register/verify",
                json={"invite": "inv-race", "credential": credential},
                headers=headers,
            )
        except BaseException as exc:  # noqa: BLE001 — re-raised in the main thread
            failures.append(exc)
            barrier.abort()  # don't strand the other thread on the barrier

    t1 = threading.Thread(target=call, args=("r1", cred_a, {}))
    t2 = threading.Thread(target=call, args=("r2", cred_b, {"origin": ORIGIN}))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    if failures:
        raise failures[0]
    statuses = sorted([results["r1"].status_code, results["r2"].status_code])
    assert statuses == [200, 410]
    creds = repo._conn.execute(
        "SELECT COUNT(*) AS n FROM credentials WHERE user_id = ?", (max_id,)
    ).fetchone()["n"]
    assert creds == 1


def test_invite_expiry(monkeypatch):
    """SEAMED — an expired invite is rejected."""
    c, app = make_auth_client()
    repo = app.state.auth_repo
    max_id = repo.get_user_by_name("alice")["id"]
    repo.create_invite(hash_token("old"), max_id, 3600)
    # Force expiry into the past.
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    repo._conn.execute(
        "UPDATE invites SET expires_at = ? WHERE token_hash = ?",
        (past, hash_token("old")),
    )
    repo._conn.commit()
    r = c.post("/auth/register/options", json={"invite": "old"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# SEAMED: registration → session
# ---------------------------------------------------------------------------


def _register(c, app, monkeypatch, invite="inv1", cred_id="cred1"):
    repo = app.state.auth_repo
    max_id = repo.get_user_by_name("alice")["id"]
    repo.create_invite(hash_token(invite), max_id, 3600)
    repo.store_challenge("Q0hBTA", "register", 300)
    monkeypatch.setattr(authn, "verify_registration", fake_register())
    cred = make_credential("Q0hBTA", cred_id=cred_id)
    return c.post("/auth/register/verify",
                  json={"invite": invite, "credential": cred})


def test_register_issues_session_cookie(monkeypatch):
    """SEAMED — a successful registration starts a session and sets the
    HttpOnly cookie."""
    c, app = make_auth_client()
    r = _register(c, app, monkeypatch)
    assert r.status_code == 200
    assert r.json()["user"] == "alice"
    set_cookie = r.headers["set-cookie"]
    assert SESSION_COOKIE in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()
    assert c.cookies.get(SESSION_COOKIE)


def test_session_cookie_authenticates_api(monkeypatch):
    """SEAMED — the session cookie alone authenticates a normal API call (no
    bearer)."""
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    r = c.get("/api/list")  # cookie in jar, no Authorization header
    assert r.status_code == 200


def test_session_token_stored_hashed(monkeypatch):
    """SEAMED — the raw session token is never stored; only its sha256."""
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    raw = c.cookies.get(SESSION_COOKIE)
    repo = app.state.auth_repo
    stored = repo._conn.execute("SELECT token_hash FROM sessions").fetchone()
    assert stored["token_hash"] != raw
    assert stored["token_hash"] == hash_token(raw)


def test_challenge_single_use(monkeypatch):
    """SEAMED — a challenge cannot be replayed after it is consumed."""
    c, app = make_auth_client()
    repo = app.state.auth_repo
    max_id = repo.get_user_by_name("alice")["id"]
    repo.create_invite(hash_token("inv1"), max_id, 3600)
    repo.create_invite(hash_token("inv2"), max_id, 3600)
    repo.store_challenge("Q0hBTA", "register", 300)
    monkeypatch.setattr(authn, "verify_registration", fake_register())
    cred = make_credential("Q0hBTA")
    assert c.post("/auth/register/verify",
                  json={"invite": "inv1", "credential": cred}).status_code == 200
    # Same challenge replayed with a fresh invite → rejected.
    cred2 = make_credential("Q0hBTA", cred_id="cred2")
    r = c.post("/auth/register/verify",
               json={"invite": "inv2", "credential": cred2},
               headers={"origin": ORIGIN})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# SEAMED: sessions list / revoke / logout / expiry / rolling
# ---------------------------------------------------------------------------


def test_sessions_list_marks_current(monkeypatch):
    """SEAMED — the caller's own session is flagged current."""
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    r = c.get("/auth/sessions")
    assert r.status_code == 200
    sessions = r.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["current"] is True


def test_logout_revokes_and_clears(monkeypatch):
    """SEAMED — logout revokes the session; the cookie no longer authenticates."""
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    r = c.post("/auth/logout", headers={"origin": ORIGIN})
    assert r.status_code == 200
    # The session is revoked server-side; the cookie no longer authenticates.
    r2 = c.get("/api/list")
    assert r2.status_code == 401


def test_session_revoke_by_id(monkeypatch):
    """SEAMED — a session can be revoked by id and stops authenticating."""
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    sid = c.get("/auth/sessions").json()["sessions"][0]["id"]
    r = c.delete(f"/auth/sessions/{sid}", headers={"origin": ORIGIN})
    assert r.status_code == 204
    assert c.get("/api/list").status_code == 401


def test_cannot_revoke_other_users_session(monkeypatch):
    """SEAMED — revoking a session that isn't yours is a 404."""
    c, app = make_auth_client()
    repo = app.state.auth_repo
    bob_id = repo.get_user_by_name("bob")["id"]
    other = repo.create_session(hash_token("x"), bob_id, 60, "bob-device")
    _register(c, app, monkeypatch)  # caller is alice
    r = c.delete(f"/auth/sessions/{other}", headers={"origin": ORIGIN})
    assert r.status_code == 404


def test_expired_session_rejected(monkeypatch):
    """SEAMED — a session past its expiry no longer authenticates."""
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    repo = app.state.auth_repo
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    repo._conn.execute("UPDATE sessions SET expires_at = ?", (past,))
    repo._conn.commit()
    assert c.get("/api/list").status_code == 401


def test_rolling_expiry_extends_past_half_life():
    """SEAMED — touch_session extends expiry once inside the second half of the
    window, but leaves a fresh session's expiry alone."""
    _, app = make_auth_client()
    repo = app.state.auth_repo
    uid = repo.get_user_by_name("alice")["id"]
    sid = repo.create_session(hash_token("s"), uid, 60, "ua")

    fresh = repo._conn.execute(
        "SELECT expires_at FROM sessions WHERE id = ?", (sid,)
    ).fetchone()["expires_at"]
    repo.touch_session(sid, 60)
    unchanged = repo._conn.execute(
        "SELECT expires_at FROM sessions WHERE id = ?", (sid,)
    ).fetchone()["expires_at"]
    assert unchanged == fresh  # still in first half → not extended

    # Move expiry to just inside the half-life window, then touch.
    near = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    repo._conn.execute(
        "UPDATE sessions SET expires_at = ? WHERE id = ?", (near, sid)
    )
    repo._conn.commit()
    repo.touch_session(sid, 60)
    extended = repo._conn.execute(
        "SELECT expires_at FROM sessions WHERE id = ?", (sid,)
    ).fetchone()["expires_at"]
    assert extended > near


# ---------------------------------------------------------------------------
# SEAMED: principal precedence + CSRF + SSE cookie
# ---------------------------------------------------------------------------


def test_bearer_precedes_cookie(monkeypatch):
    """SEAMED — bearer is evaluated first: a valid cookie plus an INVALID
    bearer still 401s (bearer wins and fails)."""
    c, app = make_auth_client()
    _register(c, app, monkeypatch)  # jar now holds a valid session cookie
    r = c.get("/api/list", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_csrf_origin_required_with_cookie(monkeypatch):
    """SEAMED — a state-changing /auth call carrying a cookie but a mismatched
    Origin is refused."""
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    r = c.post("/auth/logout", headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_sse_accepts_session_cookie(monkeypatch):
    """SEAMED — the SSE endpoint's auth resolves a human via the session
    cookie (EventSource sends it same-origin)."""
    from trug.routes.events import events_principal

    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    token = c.cookies.get(SESSION_COOKIE)

    req = types.SimpleNamespace(
        cookies={SESSION_COOKIE: token},
        headers={},
        query_params={},
        app=app,
        state=types.SimpleNamespace(),
    )
    principal = events_principal(req)
    assert principal.name == "alice"
    assert principal.source == "pwa"


# ---------------------------------------------------------------------------
# REAL: py_webauthn library vectors
# ---------------------------------------------------------------------------

# Registration vector — from py_webauthn's own test suite (none attestation).
_REG_CREDENTIAL = json.loads(
    """{
    "id": "9y1xA8Tmg1FEmT-c7_fvWZ_uoTuoih3OvR45_oAK-cwHWhAbXrl2q62iLVTjiyEZ7O7n-CROOY494k7Q3xrs_w",
    "rawId": "9y1xA8Tmg1FEmT-c7_fvWZ_uoTuoih3OvR45_oAK-cwHWhAbXrl2q62iLVTjiyEZ7O7n-CROOY494k7Q3xrs_w",
    "response": {
        "attestationObject": "o2NmbXRkbm9uZWdhdHRTdG10oGhhdXRoRGF0YVjESZYN5YgOjGh0NBcPZHZgW4_krrmihjLHmVzzuoMdl2NFAAAAFwAAAAAAAAAAAAAAAAAAAAAAQPctcQPE5oNRRJk_nO_371mf7qE7qIodzr0eOf6ACvnMB1oQG165dqutoi1U44shGezu5_gkTjmOPeJO0N8a7P-lAQIDJiABIVggSFbUJF-42Ug3pdM8rDRFu_N5oiVEysPDB6n66r_7dZAiWCDUVnB39FlGypL-qAoIO9xWHtJygo2jfDmHl-_eKFRLDA",
        "clientDataJSON": "eyJ0eXBlIjoid2ViYXV0aG4uY3JlYXRlIiwiY2hhbGxlbmdlIjoiVHdON240V1R5R0tMYzRaWS1xR3NGcUtuSE00bmdscXN5VjBJQ0psTjJUTzlYaVJ5RnRya2FEd1V2c3FsLWdrTEpYUDZmbkYxTWxyWjUzTW00UjdDdnciLCJvcmlnaW4iOiJodHRwOi8vbG9jYWxob3N0OjUwMDAiLCJjcm9zc09yaWdpbiI6ZmFsc2V9"
    },
    "type": "public-key",
    "clientExtensionResults": {},
    "transports": ["nfc", "usb"]
}"""
)
_REG_CHALLENGE = "TwN7n4WTyGKLc4ZY-qGsFqKnHM4nglqsyV0ICJlN2TO9XiRyFtrkaDwUvsql-gkLJXP6fnF1MlrZ53Mm4R7Cvw"

# EC2 authentication vector — from py_webauthn's own test suite.
_AUTH_CREDENTIAL = json.loads(
    """{
    "id": "EDx9FfAbp4obx6oll2oC4-CZuDidRVV4gZhxC529ytlnqHyqCStDUwfNdm1SNHAe3X5KvueWQdAX3x9R1a2b9Q",
    "rawId": "EDx9FfAbp4obx6oll2oC4-CZuDidRVV4gZhxC529ytlnqHyqCStDUwfNdm1SNHAe3X5KvueWQdAX3x9R1a2b9Q",
    "response": {
        "authenticatorData": "SZYN5YgOjGh0NBcPZHZgW4_krrmihjLHmVzzuoMdl2MBAAAATg",
        "clientDataJSON": "eyJjaGFsbGVuZ2UiOiJ4aTMwR1BHQUZZUnhWRHBZMXNNMTBEYUx6VlFHNjZudi1fN1JVYXpIMHZJMll2RzhMWWdERW52TjVmWlpOVnV2RUR1TWk5dGUzVkxxYjQyTjBma0xHQSIsImNsaWVudEV4dGVuc2lvbnMiOnt9LCJoYXNoQWxnb3JpdGhtIjoiU0hBLTI1NiIsIm9yaWdpbiI6Imh0dHA6Ly9sb2NhbGhvc3Q6NTAwMCIsInR5cGUiOiJ3ZWJhdXRobi5nZXQifQ",
        "signature": "MEUCIGisVZOBapCWbnJJvjelIzwpixxIwkjCCb5aCHafQu68AiEA88v-2pJNNApPFwAKFiNuf82-2hBxYW5kGwVweeoxCwo"
    },
    "type": "public-key",
    "clientExtensionResults": {}
}"""
)
_AUTH_CHALLENGE = "xi30GPGAFYRxVDpY1sM10DaLzVQG66nv-_7RUazH0vI2YvG8LYgDEnvN5fZZNVuvEDuMi9te3VLqb42N0fkLGA"
_AUTH_PUBLIC_KEY = "pQECAyYgASFYIIeDTe-gN8A-zQclHoRnGFWN8ehM1b7yAsa8I8KIvmplIlgg4nFGT5px8o6gpPZZhO01wdy9crDSA_Ngtkx0vGpvPHI"
_AUTH_CRED_ID = "EDx9FfAbp4obx6oll2oC4-CZuDidRVV4gZhxC529ytlnqHyqCStDUwfNdm1SNHAe3X5KvueWQdAX3x9R1a2b9Q"


def test_real_registration_verify_stores_credential():
    """REAL — the actual py_webauthn attestation verifier runs; a valid vector
    stores the credential (sign_count 23) and starts a session."""
    c, app = make_auth_client(origin="http://localhost:5000")
    repo = app.state.auth_repo
    max_id = repo.get_user_by_name("alice")["id"]
    repo.create_invite(hash_token("real"), max_id, 3600)
    repo.store_challenge(_REG_CHALLENGE, "register", 300)

    r = c.post("/auth/register/verify",
               json={"invite": "real", "credential": _REG_CREDENTIAL})
    assert r.status_code == 200, r.text
    assert c.cookies.get(SESSION_COOKIE)
    stored = repo._conn.execute(
        "SELECT sign_count FROM credentials"
    ).fetchone()
    assert stored["sign_count"] == 23


def test_real_registration_origin_mismatch_rejected():
    """REAL — the library rejects a vector whose clientDataJSON origin differs
    from the configured TRUG_ORIGIN."""
    c, app = make_auth_client(origin="http://localhost:9999")
    repo = app.state.auth_repo
    max_id = repo.get_user_by_name("alice")["id"]
    repo.create_invite(hash_token("real"), max_id, 3600)
    repo.store_challenge(_REG_CHALLENGE, "register", 300)

    r = c.post("/auth/register/verify",
               json={"invite": "real", "credential": _REG_CREDENTIAL})
    assert r.status_code == 400


def _seed_auth_credential(app, sign_count):
    repo = app.state.auth_repo
    from webauthn import base64url_to_bytes
    max_id = repo.get_user_by_name("alice")["id"]
    repo.add_credential(
        max_id, _AUTH_CRED_ID, base64url_to_bytes(_AUTH_PUBLIC_KEY), sign_count, None
    )
    repo.store_challenge(_AUTH_CHALLENGE, "auth", 300)


def test_real_login_requires_user_verification(monkeypatch):
    """REAL — production always requests require_user_verification=True from
    the real py_webauthn verifier. The published assertion vector (from
    py_webauthn's own test suite) predates UV enforcement: decoding its
    authenticatorData flags byte gives UP=1, UV=0. So against the real,
    unmodified production seam this otherwise-valid vector is correctly
    rejected (401) — proof UV enforcement is wired through to the library,
    not just present in source. This spy wraps the real library call (rather
    than faking it) so the crypto/signature path still actually runs; it
    only observes the kwargs to confirm require_user_verification=True was
    requested."""
    calls: dict = {}
    original = authn.verify_authentication_response

    def spy(**kwargs):
        calls.update(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(authn, "verify_authentication_response", spy)

    c, app = make_auth_client(origin="http://localhost:5000")
    _seed_auth_credential(app, sign_count=77)

    r = c.post("/auth/login/verify", json={"credential": _AUTH_CREDENTIAL})
    assert r.status_code == 401
    assert calls.get("require_user_verification") is True


def test_real_authentication_verify_and_sign_count_update(monkeypatch):
    """REAL — the actual assertion verifier (signature, origin, sign-count
    bookkeeping) runs against the real py_webauthn library. The published
    vector predates UV enforcement (see test_real_login_requires_user_
    verification for the flag decode), so it cannot satisfy
    require_user_verification=True; this test relaxes that one requirement
    at the seam — production itself is untouched and always passes True
    (asserted separately above) — purely so the rest of the real
    verification path (signature + sign-count advance 77 → 78) can still be
    exercised end to end against real vectors."""
    monkeypatch.setattr(
        authn,
        "verify_authentication",
        lambda credential, expected_challenge, public_key, sign_count, settings: (
            verify_authentication_response(
                credential=credential,
                expected_challenge=expected_challenge,
                expected_rp_id=settings.rp_id,
                expected_origin=settings.origin,
                credential_public_key=public_key,
                credential_current_sign_count=sign_count,
                require_user_verification=False,
            )
        ),
    )
    c, app = make_auth_client(origin="http://localhost:5000")
    _seed_auth_credential(app, sign_count=77)

    r = c.post("/auth/login/verify", json={"credential": _AUTH_CREDENTIAL})
    assert r.status_code == 200, r.text
    assert c.cookies.get(SESSION_COOKIE)
    new = app.state.auth_repo._conn.execute(
        "SELECT sign_count FROM credentials WHERE credential_id = ?",
        (_AUTH_CRED_ID,),
    ).fetchone()["sign_count"]
    assert new == 78


def test_real_sign_count_regression_rejected(monkeypatch):
    """REAL — cloned-authenticator guard: an assertion whose sign count does
    not advance past the stored value is rejected by the library (401). Same
    UV relaxation as test_real_authentication_verify_and_sign_count_update
    (see its docstring): without it, this vector 401s on UV before the sign
    count is ever compared, which would test the wrong thing."""
    monkeypatch.setattr(
        authn,
        "verify_authentication",
        lambda credential, expected_challenge, public_key, sign_count, settings: (
            verify_authentication_response(
                credential=credential,
                expected_challenge=expected_challenge,
                expected_rp_id=settings.rp_id,
                expected_origin=settings.origin,
                credential_public_key=public_key,
                credential_current_sign_count=sign_count,
                require_user_verification=False,
            )
        ),
    )
    c, app = make_auth_client(origin="http://localhost:5000")
    _seed_auth_credential(app, sign_count=78)  # incoming vector is also 78

    r = c.post("/auth/login/verify", json={"credential": _AUTH_CREDENTIAL})
    assert r.status_code == 401


def test_login_unknown_credential_rejected():
    """SEAMED — an assertion for an unregistered credential id is a 401."""
    c, _ = make_auth_client()
    cred = make_credential("Q0hBTA", cred_id="ghost")
    r = c.post("/auth/login/verify", json={"credential": cred})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# household users
# ---------------------------------------------------------------------------


def test_users_requires_session():
    """GET /auth/users is session-only; anonymous is 401."""
    c, _ = make_auth_client()
    assert c.get("/auth/users").status_code == 401


def test_users_rejects_bearer():
    """A machine bearer cannot read the roster — session only."""
    c, _ = make_auth_client()
    r = c.get("/auth/users", headers={"Authorization": "Bearer tok-ring"})
    assert r.status_code == 401


def test_users_returns_roster_with_enrolled_flags(monkeypatch):
    """A cookie-authed human gets the dynamic roster: alice enrolled (registered),
    bob pending, plus ``me`` marking the caller."""
    c, app = make_auth_client()
    _register(c, app, monkeypatch)  # alice registers -> enrolled + session cookie
    r = c.get("/auth/users")
    assert r.status_code == 200
    body = r.json()
    assert body["me"] == "alice"
    by_name = {u["name"]: u for u in body["users"]}
    assert by_name["alice"]["enrolled"] is True
    assert by_name["alice"]["credential_count"] == 1
    assert by_name["bob"]["enrolled"] is False


# ---------------------------------------------------------------------------
# connections (self-serve ring + MCP setup)
# ---------------------------------------------------------------------------


def test_connections_requires_session_cookie(monkeypatch):
    """SEAMED — a cookie-authed human gets MCP + webhook URLs and the machine
    tokens, built from Settings."""
    c, app = make_auth_client()
    _register(c, app, monkeypatch)  # jar now holds a session cookie
    r = c.get("/auth/connections")
    assert r.status_code == 200
    body = r.json()
    assert body["mcp_url"] == f"{ORIGIN}/mcp"
    assert body["webhook_url"] == f"{ORIGIN}/api/capture"
    assert body["mcp_token"] == "tok-mcp"
    assert body["ring_token"] == "tok-ring"


def test_connections_rejects_bearer():
    """A machine/legacy bearer must NOT be able to enumerate the other tokens —
    connections is a human-only (session) endpoint, so a bearer is 401."""
    c, _ = make_auth_client()
    for tok in ("tok-mcp", "tok-ring", "tok-alice"):
        r = c.get("/auth/connections", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401, tok


def test_connections_rejects_anonymous():
    c, _ = make_auth_client()
    assert c.get("/auth/connections").status_code == 401


# ---------------------------------------------------------------------------
# ceremony logging
# ---------------------------------------------------------------------------


def test_registration_failure_is_logged(monkeypatch, caplog):
    """A caught registration ceremony exception is logged (with context) before
    the sanitized 400 — and the log carries no challenge/secret."""
    c, app = make_auth_client()
    repo = app.state.auth_repo
    max_id = repo.get_user_by_name("alice")["id"]
    repo.create_invite(hash_token("inv1"), max_id, 3600)
    repo.store_challenge("Q0hBTA", "register", 300)

    def boom(credential, expected_challenge, settings):
        raise ValueError("attestation exploded")

    monkeypatch.setattr(authn, "verify_registration", boom)
    cred = make_credential("Q0hBTA")
    with caplog.at_level("WARNING", logger="trug.authn"):
        r = c.post("/auth/register/verify",
                   json={"invite": "inv1", "credential": cred})
    assert r.status_code == 400
    rec = next(r for r in caplog.records if r.name == "trug.authn")
    assert "registration ceremony failed" in rec.getMessage()
    assert "alice" in rec.getMessage()
    assert "Q0hBTA" not in rec.getMessage()  # never the challenge


# ---------------------------------------------------------------------------
# bootstrap: first-user claim
# ---------------------------------------------------------------------------


def test_bootstrap_state_reflects_roster():
    """The unauthenticated probe reports claimable while the roster is empty and
    flips to not-claimable once a user exists."""
    c, app = make_auth_client(seed=())
    assert c.get("/auth/bootstrap/state").json()["claimable"] is True
    app.state.auth_repo.seed_users(["alice"])
    assert c.get("/auth/bootstrap/state").json()["claimable"] is False


def test_bootstrap_state_reports_claimable_during_a_recovery_reopen():
    """A full lockout leaves users on the roster with no working credential, so
    `recover --reset-bootstrap` re-opens the claim. The probe must agree with
    the guard the claim endpoints actually use — otherwise the gate tells a
    locked-out operator the instance is claimed while the claim in fact works,
    hiding the onboarding exactly when it is needed most."""
    c, app = make_auth_client(seed=())
    app.state.auth_repo.seed_users(["alice"])
    assert c.get("/auth/bootstrap/state").json()["claimable"] is False

    app.state.auth_repo.reopen_bootstrap()
    assert app.state.auth_repo.bootstrap_reopen_active() is True
    assert c.get("/auth/bootstrap/state").json()["claimable"] is True


def test_bootstrap_options_does_not_persist_user():
    """SEAMED — options returns a challenge but does NOT create the user yet, so
    the flow stays open (user_count 0) until verify completes atomically."""
    c, app = make_auth_client(seed=())
    r = c.post("/auth/bootstrap/claim/options", json={"name": "alice"},
               headers={"Authorization": "Bearer boot-secret"})
    assert r.status_code == 200
    assert r.json()["challenge"]
    assert app.state.auth_repo.user_count() == 0


def test_bootstrap_options_requires_token():
    """SEAMED — options 403s without the bootstrap token, and on a wrong one."""
    c, _ = make_auth_client(seed=())
    assert c.post("/auth/bootstrap/claim/options", json={"name": "x"}).status_code == 403
    r = c.post("/auth/bootstrap/claim/options", json={"name": "x"},
               headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 403


def test_bootstrap_claim_creates_first_user_and_session(monkeypatch):
    """SEAMED — a valid claim creates user #1, stores the credential, and opens
    a session cookie; the roster now has exactly one enrolled user."""
    c, app = make_auth_client(seed=())
    repo = app.state.auth_repo
    repo.store_challenge("Q0hBTA", "bootstrap", 300)
    monkeypatch.setattr(authn, "verify_registration", fake_register())
    cred = make_credential("Q0hBTA")
    r = c.post("/auth/bootstrap/claim/verify",
               json={"name": "alice", "credential": cred},
               headers={"Authorization": "Bearer boot-secret"})
    assert r.status_code == 200, r.text
    assert r.json()["user"] == "alice"
    assert c.cookies.get(SESSION_COOKIE)
    assert repo.user_count() == 1
    assert repo.list_users()[0]["enrolled"] is True


def test_bootstrap_verify_requires_token(monkeypatch):
    """SEAMED — verify 403s without (or with a wrong) bootstrap token, and no
    user is created."""
    c, app = make_auth_client(seed=())
    app.state.auth_repo.store_challenge("Q0hBTA", "bootstrap", 300)
    monkeypatch.setattr(authn, "verify_registration", fake_register())
    cred = make_credential("Q0hBTA")
    assert c.post("/auth/bootstrap/claim/verify",
                  json={"name": "alice", "credential": cred}).status_code == 403
    r = c.post("/auth/bootstrap/claim/verify",
               json={"name": "alice", "credential": cred},
               headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 403
    assert app.state.auth_repo.user_count() == 0


def test_bootstrap_403s_once_any_user_exists(monkeypatch):
    """SEAMED — the one-time guard: with users already on the roster, both claim
    endpoints 403 even with the correct token."""
    c, _ = make_auth_client()  # seeds alice, bob
    r = c.post("/auth/bootstrap/claim/options", json={"name": "x"},
               headers={"Authorization": "Bearer boot-secret"})
    assert r.status_code == 403
    cred = make_credential("Q0hBTA")
    r2 = c.post("/auth/bootstrap/claim/verify",
                json={"name": "x", "credential": cred},
                headers={"Authorization": "Bearer boot-secret"})
    assert r2.status_code == 403


def test_bootstrap_is_one_time(monkeypatch):
    """SEAMED — full options→verify roundtrip claims the first account; a second
    attempt afterwards 403s and the state probe reports not-claimable."""
    c, app = make_auth_client(seed=())
    r = c.post("/auth/bootstrap/claim/options", json={"name": "ada"},
               headers={"Authorization": "Bearer boot-secret"})
    challenge = r.json()["challenge"]
    monkeypatch.setattr(authn, "verify_registration", fake_register())
    cred = make_credential(challenge)
    r2 = c.post("/auth/bootstrap/claim/verify",
                json={"name": "ada", "credential": cred},
                headers={"Authorization": "Bearer boot-secret"})
    assert r2.status_code == 200, r2.text
    assert c.get("/auth/bootstrap/state").json()["claimable"] is False
    # A second claim (now carrying ada's cookie) is refused.
    r3 = c.post("/auth/bootstrap/claim/options", json={"name": "eve"},
                headers={"Authorization": "Bearer boot-secret", "origin": ORIGIN})
    assert r3.status_code == 403


def test_real_bootstrap_verify_stores_credential():
    """REAL — the actual py_webauthn attestation verifier runs for the bootstrap
    claim; a valid vector creates the first user with the credential."""
    c, app = make_auth_client(origin="http://localhost:5000", seed=())
    repo = app.state.auth_repo
    repo.store_challenge(_REG_CHALLENGE, "bootstrap", 300)
    r = c.post("/auth/bootstrap/claim/verify",
               json={"name": "alice", "credential": _REG_CREDENTIAL},
               headers={"Authorization": "Bearer boot-secret"})
    assert r.status_code == 200, r.text
    assert c.cookies.get(SESSION_COOKIE)
    assert repo.get_user_by_name("alice") is not None
    stored = repo._conn.execute("SELECT sign_count FROM credentials").fetchone()
    assert stored["sign_count"] == 23


# ---------------------------------------------------------------------------
# members: remove / guards
# ---------------------------------------------------------------------------


def test_remove_member_requires_session():
    """DELETE /auth/members/{name} is session-only; bearer/anonymous is 401."""
    c, _ = make_auth_client()
    assert c.delete("/auth/members/bob").status_code == 401
    r = c.delete("/auth/members/bob", headers={"Authorization": "Bearer tok-ring"})
    assert r.status_code == 401


def test_remove_member_csrf_origin_required(monkeypatch):
    """A cookie-carrying delete with a mismatched Origin is refused."""
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    r = c.delete("/auth/members/bob", headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_remove_pending_member(monkeypatch):
    """A pending (unenrolled) member can be removed — the revoke-invite path."""
    c, app = make_auth_client()
    _register(c, app, monkeypatch)  # alice enrolled + session; bob still pending
    r = c.delete("/auth/members/bob", headers={"origin": ORIGIN})
    assert r.status_code == 204
    assert app.state.auth_repo.get_user_by_name("bob") is None


def test_remove_unknown_member_404(monkeypatch):
    c, app = make_auth_client()
    _register(c, app, monkeypatch)
    r = c.delete("/auth/members/ghost", headers={"origin": ORIGIN})
    assert r.status_code == 404


def test_cannot_remove_last_enrolled_member(monkeypatch):
    """The last enrolled member can't be removed (would lock the household out)."""
    c, app = make_auth_client(seed=("alice",))
    _register(c, app, monkeypatch)  # alice is the only enrolled member
    r = c.delete("/auth/members/alice", headers={"origin": ORIGIN})
    assert r.status_code == 409
    assert app.state.auth_repo.get_user_by_name("alice") is not None


def test_remove_self_ends_session(monkeypatch):
    """Removing yourself is allowed when you're not the last enrolled member, and
    it ends your session (the API no longer authenticates)."""
    c, app = make_auth_client(seed=("alice", "bob"))
    _register(c, app, monkeypatch)  # alice enrolled + session cookie
    repo = app.state.auth_repo
    bob_id = repo.get_user_by_name("bob")["id"]
    repo.add_credential(bob_id, "bobcred", b"pubkey", 0, None)  # bob also enrolled
    r = c.delete("/auth/members/alice", headers={"origin": ORIGIN})
    assert r.status_code == 204
    assert repo.get_user_by_name("alice") is None
    assert c.get("/api/list").status_code == 401


# ---------------------------------------------------------------------------
# auth_repo: atomic first-user claim (C1) + last-member removal (C2)
# ---------------------------------------------------------------------------


def test_claim_first_user_credential_failure_leaves_roster_claimable():
    """C1 — a failure inside the atomic claim (here the credential insert collides
    on a UNIQUE credential_id, standing in for add_credential failing after the
    user row was inserted in the SAME transaction) rolls the whole thing back:
    no half-created user with zero credentials. The roster stays empty, so
    bootstrap is still claimable rather than permanently bricked."""
    repo = AuthRepository(":memory:")
    # A credential already owns this id, so the claim's own credential insert
    # fails partway through the transaction, after the user insert.
    repo._conn.execute(
        "INSERT INTO credentials "
        "(id, user_id, credential_id, public_key, sign_count, transports, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("c0", "ghost", "dupcred", b"pk", 0, None, "2020-01-01T00:00:00+00:00"),
    )
    repo._conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        repo.claim_first_user(
            "alice", "dupcred", b"pk", 0, None, "sess-hash", 30, None
        )
    # Rolled back: the user insert did NOT survive → still zero users, bootstrap
    # re-openable. (The pre-seeded ghost credential is unrelated to the claim.)
    assert repo.user_count() == 0
    assert repo.get_user_by_name("alice") is None


def test_claim_first_user_concurrent_double_claim_exactly_one():
    """C1 — two genuinely concurrent claims (two threads released together by a
    barrier) can't both create a first user: the method re-checks COUNT(*)==0
    under the write lock in the same transaction as the inserts, so exactly one
    caller gets a user and the other gets None. One user, one credential,
    one enrolled member — never two."""
    repo = AuthRepository(":memory:")
    barrier = threading.Barrier(2, timeout=5)
    results: dict[str, object] = {}

    def call(key: str, cred: str) -> None:
        barrier.wait()
        results[key] = repo.claim_first_user(
            f"user-{key}", cred, b"pk", 0, None, f"hash-{key}", 30, None
        )

    t1 = threading.Thread(target=call, args=("a", "credA"))
    t2 = threading.Thread(target=call, args=("b", "credB"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    winners = [r for r in (results["a"], results["b"]) if r is not None]
    losers = [r for r in (results["a"], results["b"]) if r is None]
    assert len(winners) == 1
    assert len(losers) == 1
    assert repo.user_count() == 1
    assert repo.enrolled_count() == 1


# ---------------------------------------------------------------------------
# auth_repo: atomic invite redemption (invite + credential in one transaction)
# ---------------------------------------------------------------------------


def _invited_repo(token_hash="inv-hash"):
    """A repo with one pending user holding one unused invite."""
    repo = AuthRepository(":memory:")
    repo.seed_users(["alice"])
    uid = repo.get_user_by_name("alice")["id"]
    repo.create_invite(token_hash, uid, 3600)
    return repo, uid


def test_redeem_invite_with_credential_enrols_and_burns_the_invite():
    """The happy path: one call both spends the invite and writes the passkey,
    and invalidates the challenges minted against that invite (so a stale
    register challenge can't be replayed after redemption)."""
    repo, uid = _invited_repo()
    repo.store_challenge("chal", "register", 300, invite_token_hash="inv-hash")

    assert repo.redeem_invite_with_credential(
        "inv-hash", "cred-new", b"pk", 5, "internal"
    ) is True

    assert repo.get_valid_invite("inv-hash") is None
    cred = repo.get_credential("cred-new")
    assert cred is not None and cred["user_id"] == uid
    assert cred["sign_count"] == 5 and cred["transports"] == "internal"
    # The invite's outstanding challenge is dead too.
    assert repo.consume_challenge("chal", "register") is False


def test_redeem_invite_with_credential_refuses_a_spent_invite():
    """The second redeemer loses — the route's 410 — and writes no credential."""
    repo, uid = _invited_repo()
    assert repo.redeem_invite_with_credential("inv-hash", "cred-a", b"pk", 0, None)

    assert repo.redeem_invite_with_credential(
        "inv-hash", "cred-b", b"pk", 0, None
    ) is False
    assert repo.get_credential("cred-b") is None
    assert len(repo.credentials_for_user(uid)) == 1


def test_redeem_invite_with_credential_rolls_back_and_leaves_invite_redeemable():
    """THE LOCKOUT BUG. Spending the invite and writing the passkey used to be
    two transactions: anything failing between them (crash, OOM kill, container
    restart) burned the invite with no credential written, locking the invitee
    out — unrecoverable without ``trug-doctor`` when they were the household's
    route back in. Here the credential insert collides on a UNIQUE
    credential_id partway through, and the whole unit rolls back: the invite is
    still unused, no credential exists, and the same invite still enrols."""
    repo, uid = _invited_repo()
    repo._conn.execute(
        "INSERT INTO credentials "
        "(id, user_id, credential_id, public_key, sign_count, transports, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("c0", "ghost", "dupcred", b"pk", 0, None, "2020-01-01T00:00:00+00:00"),
    )
    repo._conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        repo.redeem_invite_with_credential("inv-hash", "dupcred", b"pk", 0, None)

    assert repo.get_valid_invite("inv-hash") is not None
    assert repo.credentials_for_user(uid) == []
    # And the invitee can simply retry with the same link.
    assert repo.redeem_invite_with_credential("inv-hash", "cred-ok", b"pk", 0, None)
    assert len(repo.credentials_for_user(uid)) == 1


def test_register_verify_failure_leaves_the_invite_redeemable(monkeypatch):
    """SEAMED, end-to-end: the same rollback seen through the route. A failing
    credential write must not leave the invitee holding a spent invite and no
    passkey."""
    c, app = make_auth_client()
    repo = app.state.auth_repo
    alice_id = repo.get_user_by_name("alice")["id"]
    repo.create_invite(hash_token("inv1"), alice_id, 3600)
    repo.store_challenge("Q0hBTA", "register", 300)
    repo._conn.execute(
        "INSERT INTO credentials "
        "(id, user_id, credential_id, public_key, sign_count, transports, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("c0", "ghost", "AQIDBA", b"pk", 0, None, "2020-01-01T00:00:00+00:00"),
    )
    repo._conn.commit()
    monkeypatch.setattr(authn, "verify_registration", fake_register())

    with pytest.raises(sqlite3.IntegrityError):
        c.post(
            "/auth/register/verify",
            json={"invite": "inv1", "credential": make_credential("Q0hBTA")},
        )

    assert repo.get_valid_invite(hash_token("inv1")) is not None
    assert repo.credentials_for_user(alice_id) == []


def test_delete_member_unless_last_enrolled_concurrent_last_two():
    """C2 — two concurrent removals of the two last enrolled members: the
    last-enrolled check and the delete share one transaction under the write
    lock, so exactly one succeeds and the other is refused. At least one enrolled
    member always remains — even with a PENDING user on the roster keeping
    user_count()>0 (the amplifier that, pre-fix, would have wedged bootstrap
    shut and locked the household out permanently)."""
    repo = AuthRepository(":memory:")
    alice = repo.create_user("alice")
    bob = repo.create_user("bob")
    repo.create_user("pending")  # unenrolled — keeps user_count() > 0
    repo.add_credential(alice["id"], "credA", b"pk", 0, None)
    repo.add_credential(bob["id"], "credB", b"pk", 0, None)

    barrier = threading.Barrier(2, timeout=5)
    results: dict[str, str] = {}

    def call(key: str, name: str) -> None:
        barrier.wait()
        results[key] = repo.delete_member_unless_last_enrolled(name)

    t1 = threading.Thread(target=call, args=("a", "alice"))
    t2 = threading.Thread(target=call, args=("b", "bob"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted([results["a"], results["b"]]) == ["last_enrolled", "ok"]
    # The invariant held: at least one enrolled member survives.
    assert repo.enrolled_count() >= 1


def test_delete_member_unless_last_enrolled_outcomes():
    """C2 — the three storage-layer outcomes the route maps to 404/409/204."""
    repo = AuthRepository(":memory:")
    assert repo.delete_member_unless_last_enrolled("ghost") == "not_found"
    enrolled = repo.create_user("solo")
    repo.add_credential(enrolled["id"], "solocred", b"pk", 0, None)
    # Removing the last enrolled member is refused.
    assert repo.delete_member_unless_last_enrolled("solo") == "last_enrolled"
    # A pending (unenrolled) member can always be removed.
    repo.create_user("pending")
    assert repo.delete_member_unless_last_enrolled("pending") == "ok"


def test_sign_count_regression_logged_distinctly(caplog):
    """REAL — the cloned-authenticator signal gets its own error-level log line,
    distinct from an ordinary auth failure."""
    c, app = make_auth_client(origin="http://localhost:5000")
    _seed_auth_credential(app, sign_count=78)  # incoming vector is also 78

    # Relax UV so the ceremony reaches the sign-count comparison (see the REAL
    # sign-count test above for why the published vector needs this).
    from trug.routes import authn as authn_mod

    monkeypatch_target = authn_mod
    orig = monkeypatch_target.verify_authentication

    def relaxed(credential, expected_challenge, public_key, sign_count, settings):
        return verify_authentication_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_rp_id=settings.rp_id,
            expected_origin=settings.origin,
            credential_public_key=public_key,
            credential_current_sign_count=sign_count,
            require_user_verification=False,
        )

    monkeypatch_target.verify_authentication = relaxed
    try:
        with caplog.at_level("ERROR", logger="trug.authn"):
            r = c.post("/auth/login/verify", json={"credential": _AUTH_CREDENTIAL})
    finally:
        monkeypatch_target.verify_authentication = orig
    assert r.status_code == 401
    rec = next(r for r in caplog.records if r.name == "trug.authn")
    assert rec.levelname == "ERROR"
    assert "sign-count regression" in rec.getMessage()
