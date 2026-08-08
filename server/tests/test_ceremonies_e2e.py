"""End-to-end WebAuthn ceremony tests driven by a real software authenticator.

Unlike ``test_authn.py``'s SEAMED tests (which fake the ceremony seam) and its
REAL tests (which replay py_webauthn's own static vectors through one endpoint),
these tests drive the WHOLE ceremony — options → authenticator → verify — through
the actual FastAPI routes with a live ``SoftwareAuthenticator`` producing real
P-256 keys, real ``fmt:"none"`` CBOR attestation, real clientDataJSON /
authenticatorData, and real ES256 assertion signatures. No monkeypatch of
``verify_registration`` / ``verify_authentication`` anywhere: the bytes go
through the same py_webauthn verifier production uses, with
``require_user_verification=True``.

These cover the logged-out / first-run paths that shipped a reload-loop bug
precisely because no test exercised them end to end:

* bootstrap first-user claim (options → make_credential → verify → session)
* invite → register a second device → verify
* discoverable login → assertion → verify → session authenticates the API
* the cloned-authenticator sign-count regression guard
* machine-bearer / anonymous gating on human-only routes
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from trug.app import create_app
from trug.auth import SESSION_COOKIE
from trug.config import Settings

from tests.webauthn_authenticator import SoftwareAuthenticator

RP_ID = "localhost"
ORIGIN = "http://localhost:8000"
BOOTSTRAP_TOKEN = "boot-secret-e2e"


def make_client(seed=()):
    """A pristine (zero-user) instance unless ``seed`` is given, wired with a
    known RP id / origin / bootstrap token so the software authenticator can
    speak to it."""
    settings = Settings.load(
        {
            "TRUG_TOKEN_RING": "tok-ring",
            "TRUG_TOKEN_MCP": "tok-mcp",
            "TRUG_BOOTSTRAP_TOKEN": BOOTSTRAP_TOKEN,
            "TRUG_DB_PATH": ":memory:",
            "TRUG_RP_ID": RP_ID,
            "TRUG_ORIGIN": ORIGIN,
        },
        None,
    )
    app = create_app(settings)
    if seed:
        app.state.auth_repo.seed_users(list(seed))
    return TestClient(app), app


def _origin_headers(extra=None):
    h = {"origin": ORIGIN}
    if extra:
        h.update(extra)
    return h


def _boot_headers(extra=None):
    return _origin_headers({"Authorization": f"Bearer {BOOTSTRAP_TOKEN}", **(extra or {})})


def _claim_first_user(c, name="alice"):
    """Run the full real bootstrap ceremony and return the device authenticator."""
    r = c.post(
        "/auth/bootstrap/claim/options",
        json={"name": name},
        headers=_boot_headers(),
    )
    assert r.status_code == 200, r.text
    options = r.json()
    device = SoftwareAuthenticator(origin=ORIGIN)
    cred = device.make_credential(options)
    r = c.post(
        "/auth/bootstrap/claim/verify",
        json={"name": name, "credential": cred},
        headers=_boot_headers(),
    )
    assert r.status_code == 200, r.text
    return device, r


# ---------------------------------------------------------------------------
# First-run bootstrap claim
# ---------------------------------------------------------------------------


def test_e2e_bootstrap_first_run_claim():
    """REAL e2e — a fresh, logged-out instance is claimable; the real ceremony
    enrolls user #1, sets a session cookie, and closes bootstrap for good."""
    c, app = make_client(seed=())

    # First-run probe: the gate sees a claimable instance.
    assert c.get("/auth/bootstrap/state").json()["claimable"] is True

    device, r = _claim_first_user(c, "alice")
    assert r.json()["user"] == "alice"

    # A real HttpOnly session cookie is now set and enrolls exactly one user.
    set_cookie = r.headers["set-cookie"]
    assert SESSION_COOKIE in set_cookie
    assert "httponly" in set_cookie.lower()
    assert c.cookies.get(SESSION_COOKIE)
    assert app.state.auth_repo.user_count() == 1
    assert app.state.auth_repo.list_users()[0]["enrolled"] is True

    # The instance is no longer claimable...
    assert c.get("/auth/bootstrap/state").json()["claimable"] is False

    # ...and a second claim attempt is refused (one-time, first-user-only).
    r2 = c.post(
        "/auth/bootstrap/claim/options",
        json={"name": "eve"},
        headers=_boot_headers(),
    )
    assert r2.status_code == 403


# ---------------------------------------------------------------------------
# Invite -> register a second device
# ---------------------------------------------------------------------------


def test_e2e_invite_then_register_second_device():
    """REAL e2e — a claimed member invites bob; bob's OWN authenticator runs the
    real registration ceremony against the invite; replaying the invite is
    refused."""
    c, app = make_client(seed=())
    _claim_first_user(c, "alice")  # jar now holds alice's session cookie

    # alice (session-authed) mints an invite for bob.
    r = c.post("/auth/invite", json={"name": "bob"}, headers=_origin_headers())
    assert r.status_code == 200, r.text
    invite = r.json()["invite"]
    assert r.json()["name"] == "bob"

    # bob's device fetches options and completes registration for real.
    r = c.post(
        "/auth/register/options",
        json={"invite": invite},
        headers=_origin_headers(),
    )
    assert r.status_code == 200, r.text
    bob_device = SoftwareAuthenticator(origin=ORIGIN)  # a SECOND authenticator
    cred = bob_device.make_credential(r.json())
    r = c.post(
        "/auth/register/verify",
        json={"invite": invite, "credential": cred},
        headers=_origin_headers(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["user"] == "bob"

    # bob is now an enrolled member with one credential.
    roster = {u["name"]: u for u in app.state.auth_repo.list_users()}
    assert roster["bob"]["enrolled"] is True
    assert roster["bob"]["credential_count"] == 1

    # The invite is single-use: replaying it (options) is refused.
    r = c.post(
        "/auth/register/options",
        json={"invite": invite},
        headers=_origin_headers(),
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Discoverable login
# ---------------------------------------------------------------------------


def _login(c, device):
    """Run a full discoverable-login ceremony with ``device`` and return the
    login/verify response."""
    r = c.post("/auth/login/options", json={}, headers=_origin_headers())
    assert r.status_code == 200, r.text
    assertion = device.get_assertion(r.json())
    return c.post(
        "/auth/login/verify",
        json={"credential": assertion},
        headers=_origin_headers(),
    )


def test_e2e_login_authenticates_api():
    """REAL e2e — a registered device runs the real assertion ceremony; the
    resulting session cookie authenticates both a normal API call and the
    human-only roster endpoint."""
    c, app = make_client(seed=())
    device, _ = _claim_first_user(c, "alice")

    # Drop the bootstrap session so login stands on its own.
    c.post("/auth/logout", headers=_origin_headers())
    c.cookies.clear()
    assert c.get("/api/list").status_code == 401

    r = _login(c, device)
    assert r.status_code == 200, r.text
    assert r.json()["user"] == "alice"
    assert c.cookies.get(SESSION_COOKIE)

    # The fresh session cookie authenticates the API and the roster.
    assert c.get("/api/list").status_code == 200
    users = c.get("/auth/users")
    assert users.status_code == 200
    assert users.json()["me"] == "alice"


# ---------------------------------------------------------------------------
# Sign-count regression guard (clone detection)
# ---------------------------------------------------------------------------


def test_e2e_sign_count_regression_rejected():
    """REAL e2e — after a successful login advances the stored sign count, a
    fresh assertion carrying a stale (equal) count is rejected 401 by the real
    verifier: the cloned-authenticator signal, through the whole ceremony."""
    c, app = make_client(seed=())
    device, _ = _claim_first_user(c, "alice")
    c.post("/auth/logout", headers=_origin_headers())
    c.cookies.clear()

    # First login advances the counter (0 -> 1) and is accepted.
    assert _login(c, device).status_code == 200
    c.post("/auth/logout", headers=_origin_headers())
    c.cookies.clear()

    stored_count = app.state.auth_repo.get_credential(device.credential_id_b64)[
        "sign_count"
    ]

    # A new ceremony (fresh challenge, valid signature) but with a stale, equal
    # sign count must be refused — the challenge is real so this reaches the
    # sign-count comparison rather than tripping on replay.
    r = c.post("/auth/login/options", json={}, headers=_origin_headers())
    assertion = device.get_assertion(r.json(), sign_count=stored_count)
    r = c.post(
        "/auth/login/verify",
        json={"credential": assertion},
        headers=_origin_headers(),
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Machine / anonymous gating on human-only routes
# ---------------------------------------------------------------------------


def test_e2e_machine_and_anon_gating():
    """REAL e2e context — with a genuinely enrolled household, the machine
    bearers (ring, mcp) and anonymous callers are still 401 on the human-only
    invite and roster routes; only a real session cookie passes."""
    c, app = make_client(seed=())
    device, _ = _claim_first_user(c, "alice")
    session_cookie = c.cookies.get(SESSION_COOKIE)

    # Machine bearers cannot mint invites or read the roster. These MUST be
    # cookie-less clients — the human-only routes resolve strictly via the
    # session cookie, so a bearer alongside a cookie would (correctly) ride the
    # cookie. A machine presents a bearer and nothing else.
    for tok in ("tok-ring", "tok-mcp"):
        machine = TestClient(app)
        bearer = {"Authorization": f"Bearer {tok}"}
        assert (
            machine.post(
                "/auth/invite",
                json={"name": "guest"},
                headers=_origin_headers(bearer),
            ).status_code
            == 401
        ), tok
        assert machine.get("/auth/users", headers=bearer).status_code == 401, tok

    # Anonymous (no cookie, no bearer) is likewise refused.
    anon = TestClient(app)
    assert anon.post(
        "/auth/invite", json={"name": "guest"}, headers=_origin_headers()
    ).status_code == 401
    assert anon.get("/auth/users").status_code == 401

    # Sanity: the real session cookie is what actually passes.
    assert c.get("/auth/users").status_code == 200
    assert session_cookie is not None
