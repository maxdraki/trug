"""Full-flow tests for the in-process OAuth 2.1 + CIMD authorization server.

Drives the entire browser-less flow: discovery metadata → authorize (gated on a
passkey session) → consent → code → PKCE token exchange → refresh rotation, plus
the /mcp dual-auth matrix (OAuth access token OR static token_mcp). CIMD is
exercised three ways: a DCR-registered client, a seeded known Claude client, and
a fetched CIMD document (via the ``fetch_cimd_document`` seam, faked — no
network).
"""

import json

from fastapi.testclient import TestClient

from trug import oauth
from trug.app import create_app
from trug.auth import SESSION_COOKIE, hash_token
from trug.config import Settings
from trug.oauth import compute_s256_challenge

ORIGIN = "http://localhost:8000"
MCP = {"Authorization": "Bearer tok-mcp"}
VERIFIER = "a-high-entropy-code-verifier-1234567890-abcdefghij"
CHALLENGE = compute_s256_challenge(VERIFIER)


def make_app():
    s = Settings.load(
        {
            "TRUG_TOKEN_RING": "tok-ring",
            "TRUG_TOKEN_MCP": "tok-mcp",
            "TRUG_DB_PATH": ":memory:",
            "TRUG_ORIGIN": ORIGIN,
        },
        None,
    )
    app = create_app(s)
    # The roster is dynamic now; seed the household so signed_in() can look up alice.
    app.state.auth_repo.seed_users(["alice", "bob"])
    return app


def signed_in():
    """A TestClient carrying a valid passkey session cookie for user ``alice``,
    plus the app (for repo access)."""
    app = make_app()
    c = TestClient(app)
    uid = app.state.auth_repo.get_user_by_name("alice")["id"]
    token = "sess-raw-token"
    app.state.auth_repo.create_session(hash_token(token), uid, 60, "test")
    c.cookies.set(SESSION_COOKIE, token)
    return c, app


def register_dcr(c, redirect_uris=("http://localhost/callback",), name="Test App"):
    r = c.post(
        "/oauth/register",
        json={"redirect_uris": list(redirect_uris), "client_name": name},
    )
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def authorize_params(client_id, redirect_uri="http://localhost/callback",
                     resource=None, state="xyz"):
    p = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "state": state,
        "scope": "mcp",
    }
    if resource is not None:
        p["resource"] = resource
    return p


def approve(c, client_id, **overrides):
    """GET the consent page then POST approve; return the redirect Response
    (not followed) so the caller can read the ``code``."""
    params = authorize_params(client_id, **overrides)
    g = c.get("/oauth/authorize", params=params, follow_redirects=False)
    assert g.status_code == 200, g.text
    form = {**params, "decision": "approve"}
    return c.post("/oauth/authorize", data=form, follow_redirects=False)


def code_from(resp):
    from urllib.parse import parse_qs, urlparse
    return parse_qs(urlparse(resp.headers["location"]).query)


# --- discovery metadata ----------------------------------------------------


def test_protected_resource_metadata_shape():
    c = TestClient(make_app())
    for path in (
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
    ):
        r = c.get(path)
        assert r.status_code == 200, path
        body = r.json()
        assert body["resource"] == f"{ORIGIN}/mcp"
        assert body["authorization_servers"] == [ORIGIN]


def test_authorization_server_metadata_shape():
    c = TestClient(make_app())
    for path in (
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-authorization-server/mcp",
    ):
        r = c.get(path)
        assert r.status_code == 200, path
        m = r.json()
        assert m["issuer"] == ORIGIN
        assert m["authorization_endpoint"] == f"{ORIGIN}/oauth/authorize"
        assert m["token_endpoint"] == f"{ORIGIN}/oauth/token"
        assert m["code_challenge_methods_supported"] == ["S256"]
        assert m["grant_types_supported"] == ["authorization_code", "refresh_token"]
        # CIMD selection signals Claude requires: both must be present.
        assert m["client_id_metadata_document_supported"] is True
        assert "none" in m["token_endpoint_auth_methods_supported"]
        assert m["authorization_response_iss_parameter_supported"] is True
        assert m["resource_indicators_supported"] is True
        # DCR fallback advertised as well.
        assert m["registration_endpoint"] == f"{ORIGIN}/oauth/register"


# --- authorize gate --------------------------------------------------------


def test_authorize_without_session_redirects_to_pwa_gate():
    app = make_app()
    c = TestClient(app)
    client_id = register_dcr(c)
    r = c.get("/oauth/authorize", params=authorize_params(client_id),
              follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("/?next=")
    assert "oauth%2Fauthorize" in loc  # returns here after sign-in


def test_authorize_unknown_client_400():
    c, _ = signed_in()
    r = c.get("/oauth/authorize",
              params=authorize_params("trug-client-nonexistent"),
              follow_redirects=False)
    assert r.status_code == 400


def test_authorize_bad_redirect_uri_400():
    c, _ = signed_in()
    client_id = register_dcr(c, redirect_uris=["http://localhost/callback"])
    r = c.get("/oauth/authorize",
              params=authorize_params(client_id, redirect_uri="http://evil/cb"),
              follow_redirects=False)
    assert r.status_code == 400


def test_authorize_with_session_renders_consent():
    c, _ = signed_in()
    client_id = register_dcr(c, name="Grocer Bot")
    r = c.get("/oauth/authorize", params=authorize_params(client_id),
              follow_redirects=False)
    assert r.status_code == 200
    assert "Grocer Bot" in r.text
    assert "shopping list" in r.text


def test_consent_page_has_anti_clickjacking_headers():
    c, _ = signed_in()
    client_id = register_dcr(c, name="Grocer Bot")
    r = c.get("/oauth/authorize", params=authorize_params(client_id),
              follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["content-security-policy"] == "frame-ancestors 'none'"


def test_authorize_without_session_does_not_resolve_client(monkeypatch):
    """A fetchable-looking (CIMD) client_id must not trigger any resolution
    work — and in particular no network fetch — before the session gate."""
    calls = []

    def fake_fetch(client_id):
        calls.append(client_id)
        return {
            "client_id": client_id,
            "redirect_uris": ["https://app.example/cb"],
            "client_name": "App",
        }

    monkeypatch.setattr(oauth, "fetch_cimd_document", fake_fetch)

    app = make_app()
    c = TestClient(app)
    client_id = "https://app.example/.well-known/client-metadata"
    r = c.get(
        "/oauth/authorize",
        params=authorize_params(client_id, redirect_uri="https://app.example/cb"),
        follow_redirects=False,
    )
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("/?next=")
    assert "oauth%2Fauthorize" in loc
    assert calls == []  # no CIMD fetch attempted


# --- consent → code --------------------------------------------------------


def test_approve_issues_code_with_iss_and_state():
    c, _ = signed_in()
    client_id = register_dcr(c)
    r = approve(c, client_id)
    assert r.status_code == 302
    q = code_from(r)
    assert q["code"][0]
    assert q["iss"][0] == ORIGIN  # RFC 9207
    assert q["state"][0] == "xyz"


def test_deny_redirects_with_access_denied():
    c, _ = signed_in()
    client_id = register_dcr(c)
    params = authorize_params(client_id)
    c.get("/oauth/authorize", params=params, follow_redirects=False)
    r = c.post("/oauth/authorize", data={**params, "decision": "deny"},
               follow_redirects=False)
    assert r.status_code == 302
    q = code_from(r)
    assert q["error"][0] == "access_denied"


# --- token exchange --------------------------------------------------------


def exchange(c, client_id, code, resource=None,
             redirect_uri="http://localhost/callback", verifier=VERIFIER):
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    if resource is not None:
        data["resource"] = resource
    return c.post("/oauth/token", data=data)


def test_token_exchange_happy():
    c, _ = signed_in()
    client_id = register_dcr(c)
    code = code_from(approve(c, client_id, resource=f"{ORIGIN}/mcp"))["code"][0]
    r = exchange(c, client_id, code, resource=f"{ORIGIN}/mcp")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["expires_in"] == oauth.ACCESS_TTL
    assert r.headers["cache-control"] == "no-store"


def test_token_exchange_pkce_failure():
    c, _ = signed_in()
    client_id = register_dcr(c)
    code = code_from(approve(c, client_id))["code"][0]
    r = exchange(c, client_id, code, verifier="wrong-verifier")
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_token_code_single_use():
    c, _ = signed_in()
    client_id = register_dcr(c)
    code = code_from(approve(c, client_id))["code"][0]
    assert exchange(c, client_id, code).status_code == 200
    # Replaying the same code fails.
    r = exchange(c, client_id, code)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_token_wrong_resource_rejected():
    c, _ = signed_in()
    client_id = register_dcr(c)
    code = code_from(approve(c, client_id, resource=f"{ORIGIN}/mcp"))["code"][0]
    # Ask for a different resource at the token endpoint than was authorized.
    r = exchange(c, client_id, code, resource="https://evil.example/mcp")
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_target"


def test_token_redirect_uri_mismatch_rejected():
    c, _ = signed_in()
    client_id = register_dcr(c)
    code = code_from(approve(c, client_id))["code"][0]
    r = exchange(c, client_id, code, redirect_uri="http://localhost/other")
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


# --- refresh rotation ------------------------------------------------------


def refresh(c, client_id, refresh_token):
    return c.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    })


def test_refresh_rotates_and_old_token_dies():
    c, _ = signed_in()
    client_id = register_dcr(c)
    code = code_from(approve(c, client_id, resource=f"{ORIGIN}/mcp"))["code"][0]
    first = exchange(c, client_id, code, resource=f"{ORIGIN}/mcp").json()
    rt1 = first["refresh_token"]

    r = refresh(c, client_id, rt1)
    assert r.status_code == 200, r.text
    rotated = r.json()
    assert rotated["access_token"] != first["access_token"]
    assert rotated["refresh_token"] != rt1  # rotated

    # The consumed (old) refresh token no longer works.
    again = refresh(c, client_id, rt1)
    assert again.status_code == 400
    assert again.json()["error"] == "invalid_grant"


def test_revoked_refresh_token_fails():
    c, app = signed_in()
    client_id = register_dcr(c)
    code = code_from(approve(c, client_id))["code"][0]
    rt = exchange(c, client_id, code).json()["refresh_token"]
    app.state.oauth_repo.revoke_refresh_token(hash_token(rt))
    r = refresh(c, client_id, rt)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


# --- /mcp dual auth --------------------------------------------------------


def mcp_list(c, headers):
    return c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                  headers=headers)


def test_mcp_accepts_oauth_access_token():
    c, _ = signed_in()
    client_id = register_dcr(c)
    code = code_from(approve(c, client_id, resource=f"{ORIGIN}/mcp"))["code"][0]
    access = exchange(c, client_id, code, resource=f"{ORIGIN}/mcp").json()["access_token"]
    r = mcp_list(c, {"Authorization": f"Bearer {access}"})
    assert r.status_code == 200, r.text
    assert {t["name"] for t in r.json()["result"]["tools"]}


def test_mcp_still_accepts_static_token_mcp():
    c, _ = signed_in()
    r = mcp_list(c, MCP)
    assert r.status_code == 200


def test_mcp_rejects_garbage_bearer():
    c, _ = signed_in()
    r = mcp_list(c, {"Authorization": "Bearer total-garbage"})
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers.get("WWW-Authenticate", "")


def test_mcp_rejects_access_token_for_wrong_resource():
    c, app = signed_in()
    # Mint an access token bound to a different resource directly.
    token = oauth.new_token()
    app.state.oauth_repo.create_access_token(
        hash_token(token), "trug-client-x", "user-x",
        "https://other.example/mcp", "mcp", 3600,
    )
    r = mcp_list(c, {"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


# --- CIMD paths ------------------------------------------------------------


def test_known_claude_client_resolves_without_network():
    c, _ = signed_in()
    client_id = "https://claude.ai/oauth/mcp-oauth-client-metadata"
    r = c.get(
        "/oauth/authorize",
        params=authorize_params(
            client_id, redirect_uri="https://claude.ai/api/mcp/auth_callback"
        ),
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "Claude" in r.text


def test_claude_code_loopback_port_agnostic():
    c, _ = signed_in()
    client_id = "https://claude.ai/oauth/claude-code-client-metadata"
    # Registered as http://localhost/callback; an ephemeral port must match.
    r = c.get(
        "/oauth/authorize",
        params=authorize_params(client_id, redirect_uri="http://localhost:52133/callback"),
        follow_redirects=False,
    )
    assert r.status_code == 200


def test_cimd_document_fetched_and_cached(monkeypatch):
    calls = {"n": 0}

    def fake_fetch(client_id):
        calls["n"] += 1
        return {
            "client_id": client_id,
            "client_name": "Fetched App",
            "redirect_uris": ["https://app.example/cb"],
        }

    monkeypatch.setattr(oauth, "fetch_cimd_document", fake_fetch)
    c, _ = signed_in()
    client_id = "https://app.example/.well-known/client-metadata"
    for _ in range(2):
        r = c.get(
            "/oauth/authorize",
            params=authorize_params(client_id, redirect_uri="https://app.example/cb"),
            follow_redirects=False,
        )
        assert r.status_code == 200
        assert "Fetched App" in r.text
    # Second authorize served from cache — the document was fetched once.
    assert calls["n"] == 1


def test_cimd_redirect_uri_must_match_document(monkeypatch):
    monkeypatch.setattr(
        oauth, "fetch_cimd_document",
        lambda cid: {"redirect_uris": ["https://app.example/cb"]},
    )
    c, _ = signed_in()
    client_id = "https://app.example/.well-known/client-metadata"
    r = c.get(
        "/oauth/authorize",
        params=authorize_params(client_id, redirect_uri="https://app.example/evil"),
        follow_redirects=False,
    )
    assert r.status_code == 400


# --- CIMD fetch SSRF hardening ---------------------------------------------

import pytest

CIMD_URL = "https://app.example/.well-known/client-metadata"


def _json_response(payload, status=200, content_type="application/json",
                   truncated=False):
    return {
        "status_code": status,
        "content_type": content_type,
        "body": json.dumps(payload).encode() if payload is not None else b"",
        "truncated": truncated,
    }


def _no_fetch(monkeypatch):
    """Wire _fetch_pinned to blow up: proves a rejection happened BEFORE any
    network connection was attempted."""
    def boom(*a, **k):
        raise AssertionError("network fetch must not happen")
    monkeypatch.setattr(oauth, "_fetch_pinned", boom)


def test_cimd_rejects_http_scheme(monkeypatch):
    # Resolver + fetch must never be reached for a non-https scheme.
    monkeypatch.setattr(oauth, "_resolve_ips",
                        lambda h: (_ for _ in ()).throw(AssertionError("no dns")))
    _no_fetch(monkeypatch)
    assert oauth.fetch_cimd_document("http://app.example/cimd") is None


def test_cimd_rejects_raw_ip_client_id(monkeypatch):
    _no_fetch(monkeypatch)
    assert oauth.fetch_cimd_document("https://93.184.216.34/cimd") is None
    assert oauth.fetch_cimd_document("https://[2606:2800:220:1::248]/cimd") is None


def test_cimd_rejects_literal_localhost(monkeypatch):
    _no_fetch(monkeypatch)
    assert oauth.fetch_cimd_document("https://localhost/cimd") is None


def test_cimd_rejects_non_443_port(monkeypatch):
    _no_fetch(monkeypatch)
    assert oauth.fetch_cimd_document("https://app.example:8080/cimd") is None


@pytest.mark.parametrize("addr", [
    "10.0.0.1",        # RFC1918 private
    "127.0.0.1",       # loopback (also the 127.1 shorthand's real resolution)
    "169.254.10.20",   # link-local
    "::1",             # IPv6 loopback
    "::ffff:10.0.0.1", # IPv4-mapped IPv6 of a private v4
    "fd00::1",         # IPv6 unique-local (private)
])
def test_cimd_rejects_private_resolved_ip(monkeypatch, addr):
    monkeypatch.setattr(oauth, "_resolve_ips", lambda h: [addr])
    _no_fetch(monkeypatch)  # guard must reject before any connect
    assert oauth.fetch_cimd_document(CIMD_URL) is None


def test_cimd_rejects_127_1_shorthand_via_resolver(monkeypatch):
    # '127.1' is not a raw-IP literal (unparseable), so it flows to the DNS
    # guard, where its real resolution to loopback is caught.
    monkeypatch.setattr(oauth, "_resolve_ips", lambda h: ["127.0.0.1"])
    _no_fetch(monkeypatch)
    assert oauth.fetch_cimd_document("https://127.1/cimd") is None


def test_cimd_rejects_if_any_resolved_ip_private(monkeypatch):
    # One public, one private → still rejected (ANY non-public address blocks).
    monkeypatch.setattr(oauth, "_resolve_ips", lambda h: ["93.184.216.34", "10.0.0.1"])
    _no_fetch(monkeypatch)
    assert oauth.fetch_cimd_document(CIMD_URL) is None


def test_cimd_dns_rebinding_pins_to_vetted_ip(monkeypatch):
    # Host resolves public; the fetch MUST be pinned to that exact address, not
    # re-resolve the hostname (which an attacker could rebind to an internal IP).
    monkeypatch.setattr(oauth, "_resolve_ips", lambda h: ["93.184.216.34"])
    seen = {}

    def fake_pinned(url, ip, hostname):
        seen["ip"] = ip
        seen["hostname"] = hostname
        return _json_response({"client_id": CIMD_URL,
                               "redirect_uris": ["https://app.example/cb"]})

    monkeypatch.setattr(oauth, "_fetch_pinned", fake_pinned)
    doc = oauth.fetch_cimd_document(CIMD_URL)
    assert doc is not None
    assert seen["ip"] == "93.184.216.34"      # pinned to the vetted address
    assert seen["hostname"] == "app.example"  # SNI/Host preserved


def test_cimd_rejects_redirect(monkeypatch):
    monkeypatch.setattr(oauth, "_resolve_ips", lambda h: ["93.184.216.34"])
    monkeypatch.setattr(oauth, "_fetch_pinned",
                        lambda *a: _json_response(None, status=302))
    assert oauth.fetch_cimd_document(CIMD_URL) is None


def test_cimd_rejects_oversize_body(monkeypatch):
    monkeypatch.setattr(oauth, "_resolve_ips", lambda h: ["93.184.216.34"])
    monkeypatch.setattr(oauth, "_fetch_pinned",
                        lambda *a: _json_response({"x": "y"}, truncated=True))
    assert oauth.fetch_cimd_document(CIMD_URL) is None


def test_cimd_rejects_non_json_content_type(monkeypatch):
    monkeypatch.setattr(oauth, "_resolve_ips", lambda h: ["93.184.216.34"])
    monkeypatch.setattr(
        oauth, "_fetch_pinned",
        lambda *a: _json_response({"redirect_uris": ["https://app.example/cb"]},
                                  content_type="text/html"),
    )
    assert oauth.fetch_cimd_document(CIMD_URL) is None


def test_cimd_rejects_unparseable_json(monkeypatch):
    monkeypatch.setattr(oauth, "_resolve_ips", lambda h: ["93.184.216.34"])
    monkeypatch.setattr(oauth, "_fetch_pinned", lambda *a: {
        "status_code": 200, "content_type": "application/json",
        "body": b"{not json", "truncated": False,
    })
    assert oauth.fetch_cimd_document(CIMD_URL) is None


def test_cimd_happy_path_public_ip(monkeypatch):
    monkeypatch.setattr(oauth, "_resolve_ips", lambda h: ["93.184.216.34"])
    monkeypatch.setattr(
        oauth, "_fetch_pinned",
        lambda *a: _json_response({
            "client_id": CIMD_URL,
            "client_name": "Fetched App",
            "redirect_uris": ["https://app.example/cb"],
        }),
    )
    doc = oauth.fetch_cimd_document(CIMD_URL)
    assert doc == {
        "client_id": CIMD_URL,
        "client_name": "Fetched App",
        "redirect_uris": ["https://app.example/cb"],
    }


def test_cimd_rejection_not_cached_as_success(monkeypatch):
    # A blocked fetch must not populate the post-validation cache.
    monkeypatch.setattr(oauth, "_resolve_ips", lambda h: ["10.0.0.1"])
    _no_fetch(monkeypatch)
    c, app = signed_in()
    assert oauth.resolve_client(CIMD_URL, app.state.oauth_repo) is None
    assert app.state.oauth_repo.get_cached_cimd(CIMD_URL) is None


# --- DCR --------------------------------------------------------------------


def test_dcr_register_returns_client_id():
    c = TestClient(make_app())
    r = c.post("/oauth/register",
               json={"redirect_uris": ["http://localhost/callback"],
                     "client_name": "CLI"})
    assert r.status_code == 201
    body = r.json()
    assert body["client_id"].startswith("trug-client-")
    assert body["token_endpoint_auth_method"] == "none"


def test_dcr_register_requires_redirect_uris():
    c = TestClient(make_app())
    r = c.post("/oauth/register", json={"client_name": "CLI"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_redirect_uri"


# --- DCR hardening (Fix 1.1): caps + rate limit on the unauthenticated write --


def test_dcr_rejects_too_many_redirect_uris():
    c = TestClient(make_app())
    uris = [f"https://app.example/cb{i}" for i in range(11)]
    r = c.post("/oauth/register", json={"redirect_uris": uris})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_redirect_uri"


def test_dcr_rejects_overlong_redirect_uri():
    c = TestClient(make_app())
    long_uri = "https://app.example/" + "a" * 600
    r = c.post("/oauth/register", json={"redirect_uris": [long_uri]})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_redirect_uri"


def test_dcr_rejects_non_string_redirect_uri():
    c = TestClient(make_app())
    r = c.post("/oauth/register", json={"redirect_uris": [123]})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_redirect_uri"


def test_dcr_rejects_non_absolute_redirect_uri():
    c = TestClient(make_app())
    r = c.post("/oauth/register", json={"redirect_uris": ["/relative/callback"]})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_redirect_uri"


def test_dcr_rejects_overlong_client_name():
    c = TestClient(make_app())
    r = c.post(
        "/oauth/register",
        json={"redirect_uris": ["https://app.example/cb"], "client_name": "x" * 300},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_client_metadata"


def test_dcr_malformed_ipv6_redirect_uri_returns_400_not_500():
    # I1: urlparse("https://[::1") raises ValueError *inside* urlparse, before
    # any attribute access. The unauthenticated DCR endpoint must not 500.
    c = TestClient(make_app())
    r = c.post("/oauth/register", json={"redirect_uris": ["https://[::1"]})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_redirect_uri"


def test_dcr_rejects_script_scheme_redirect_uris():
    # I4: an auth server must not store/reflect script-scheme redirect targets.
    c = TestClient(make_app())
    for uri in ("javascript://%0aalert(1)", "data://x", "vbscript://x"):
        r = c.post("/oauth/register", json={"redirect_uris": [uri]})
        assert r.status_code == 400, uri
        assert r.json()["error"] == "invalid_redirect_uri"


def test_dcr_accepts_http_and_https_redirect_uris():
    c = TestClient(make_app())
    r = c.post(
        "/oauth/register",
        json={"redirect_uris": ["https://app.example/cb", "http://127.0.0.1:1234/cb"]},
    )
    assert r.status_code == 201


def test_dcr_register_rate_limited():
    from trug.ratelimit import AUTH_LIMIT

    c = TestClient(make_app())
    body = {"redirect_uris": ["https://app.example/cb"], "client_name": "CLI"}
    statuses = [
        c.post("/oauth/register", json=body).status_code
        for _ in range(AUTH_LIMIT + 1)
    ]
    assert all(s != 429 for s in statuses[:AUTH_LIMIT])
    assert statuses[-1] == 429


def test_token_endpoint_requires_form_encoding():
    """RFC 6749: the token endpoint parses x-www-form-urlencoded. A JSON body is
    not accepted as form data → the grant_type is absent → unsupported_grant."""
    c = TestClient(make_app())
    r = c.post("/oauth/token", content=json.dumps({"grant_type": "refresh_token"}),
               headers={"Content-Type": "application/json"})
    assert r.status_code == 400
