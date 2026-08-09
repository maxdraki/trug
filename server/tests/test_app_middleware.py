"""Middleware-level tests for the observed-host recorder.

Two hard requirements the recorder must honour no matter what a proxy or an
attacker sends:

1. A malformed ``Host`` / ``X-Forwarded-Host`` must NOT 500 the request — the
   parse sits before the middleware's own try/except, so an unparseable IPv6
   literal like ``[`` would otherwise propagate to an HTTP 500 on every real
   route (passkey + OAuth included). See doctor-fixes C1.
2. The in-memory throttle map must stay bounded regardless of how many distinct
   (attacker-controllable) hosts are thrown at it. See doctor-fixes I2.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from trug.app import create_app
from trug.auth_repo import _MAX_OBSERVED_HOSTS
from trug.config import Settings


def _settings():
    return Settings.load(
        {
            "TRUG_TOKEN_RING": "tok-ring",
            "TRUG_TOKEN_MCP": "tok-mcp",
            "TRUG_BOOTSTRAP_TOKEN": "boot",
            "TRUG_DB_PATH": ":memory:",
            "TRUG_RP_ID": "trug.example.com",
            "TRUG_ORIGIN": "https://trug.example.com",
        },
        None,
    )


# --- C1: malformed host must not 500 ----------------------------------------


def test_malformed_host_on_healthz_does_not_500():
    app = create_app(_settings())
    c = TestClient(app)
    # A bare '[' is an "Invalid IPv6 URL" that makes urlsplit raise ValueError.
    r = c.get("/healthz", headers={"host": "["})
    assert r.status_code == 200
    assert app.state.auth_repo.observed_hosts() == []


def test_malformed_forwarded_host_does_not_500_and_records_nothing():
    app = create_app(_settings())
    c = TestClient(app)
    # X-Forwarded-Host WINS over Host, so a proxy-forwarded bad value must be
    # tolerated too. The handler's own status (not 500) is returned.
    r = c.get("/api/list", headers={"x-forwarded-host": "[::1"})
    assert r.status_code != 500
    assert app.state.auth_repo.observed_hosts() == []


def test_bracket_only_forwarded_host_does_not_500():
    app = create_app(_settings())
    c = TestClient(app)
    r = c.get("/api/list", headers={"x-forwarded-host": "]"})
    assert r.status_code != 500
    assert app.state.auth_repo.observed_hosts() == []


# --- I2: bounded in-memory throttle map -------------------------------------


# --- Fix 1.2: persisted "forwarded header seen" signal for the doctor --------
# C1: the signal is only trustworthy when the real socket peer is a co-located
# proxy (private/loopback/link-local). A public-internet peer sending a forged
# X-Forwarded-* header must NOT flip it (else it would drive the doctor to hand
# out TRUG_TRUSTED_PROXY_HOPS=1, letting the attacker rotate the rate-limit key).


def _loopback_client(app):
    # A co-located proxy (cloudflared/Tailscale/nginx/Railway ingress) reaches
    # the app over loopback/private, so its socket peer is a loopback IP.
    return TestClient(app, client=("127.0.0.1", 40000))


def _public_client(app):
    # A genuinely global peer (documentation ranges like 203.0.113.0/24 are
    # classified is_private by ipaddress, so they would NOT model an attacker).
    return TestClient(app, client=("8.8.8.8", 40000))


def test_forwarded_header_from_private_peer_marks_seen_signal():
    app = create_app(_settings())
    c = _loopback_client(app)
    assert app.state.auth_repo.forwarded_header_seen() is False
    c.get("/api/list", headers={"x-forwarded-for": "1.2.3.4"})
    assert app.state.auth_repo.forwarded_header_seen() is True


def test_forwarded_host_header_from_private_peer_marks_seen_signal():
    app = create_app(_settings())
    c = _loopback_client(app)
    c.get("/api/list", headers={"x-forwarded-host": "trug.example.com"})
    assert app.state.auth_repo.forwarded_header_seen() is True


def test_forwarded_header_from_public_peer_does_not_mark_seen_signal():
    app = create_app(_settings())
    c = _public_client(app)
    # A public attacker forging XFF on a direct deployment must not flip the
    # write-once proxy signal.
    c.get("/api/list", headers={"x-forwarded-for": "1.2.3.4"})
    assert app.state.auth_repo.forwarded_header_seen() is False


def test_no_forwarded_header_leaves_signal_unset():
    app = create_app(_settings())
    c = _loopback_client(app)
    c.get("/api/list")
    assert app.state.auth_repo.forwarded_header_seen() is False


def test_throttle_map_bounded_under_distinct_host_flood():
    app = create_app(_settings())
    c = TestClient(app)
    n = _MAX_OBSERVED_HOSTS * 5
    for i in range(n):
        c.get("/api/list", headers={"host": f"host{i}.example.com"})
    # The persisted table is already capped; the in-memory throttle map must be
    # capped too so distinct spoofed hosts can't grow it without bound.
    assert len(app.state.observed_host_throttle) <= _MAX_OBSERVED_HOSTS


# --- favicon: default /favicon.ico path must resolve to the PWA PNG ----------


def test_favicon_ico_serves_png_when_static_present(tmp_path):
    icons = tmp_path / "icons"
    icons.mkdir()
    (icons / "icon-192.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    s = Settings.load(
        {
            "TRUG_TOKEN_RING": "tok-ring",
            "TRUG_TOKEN_MCP": "tok-mcp",
            "TRUG_DB_PATH": ":memory:",
            "TRUG_STATIC_DIR": str(tmp_path),
        },
        None,
    )
    c = TestClient(create_app(s))
    r = c.get("/favicon.ico")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG")
