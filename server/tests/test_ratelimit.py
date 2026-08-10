"""In-process per-client rate limiter.

Defense-in-depth for an internet-exposed AS: a fixed-window limiter keyed by
(client_ip, bucket). The tests pin the behaviours that matter — deterministic
windows via an injectable clock (no real sleep), per-IP isolation, the
trusted-hops client-IP trust boundary (default ignores X-Forwarded-For), a HARD
memory bound under an IP-rotation flood, genuine thread-race safety, the fail-safe
disable switch, and end-to-end 429s on the real auth endpoints.
"""

from __future__ import annotations

import threading
import types

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from trug.app import create_app
from trug.config import Settings
from trug.ratelimit import RateLimiter, client_ip


class Clock:
    """A hand-cranked monotonic clock — no wall time, no sleeps."""

    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _request(*, xff: str | None = None, peer: str | None = "203.0.113.7"):
    headers = {"x-forwarded-for": xff} if xff is not None else {}
    client = types.SimpleNamespace(host=peer) if peer is not None else None
    return types.SimpleNamespace(headers=Headers(headers), client=client)


# --- core window behaviour --------------------------------------------------


def test_under_limit_passes_then_next_is_limited():
    clock = Clock()
    limiter = RateLimiter(time_fn=clock)
    for _ in range(3):
        assert limiter.check("1.1.1.1", "auth", limit=3, window=60) is None
    retry = limiter.check("1.1.1.1", "auth", limit=3, window=60)
    assert retry is not None
    assert 0 < retry <= 60


def test_window_resets_after_it_elapses():
    clock = Clock()
    limiter = RateLimiter(time_fn=clock)
    assert limiter.check("1.1.1.1", "auth", limit=1, window=60) is None
    assert limiter.check("1.1.1.1", "auth", limit=1, window=60) is not None
    # Advance past the window: the counter starts fresh, no real sleep involved.
    clock.advance(60)
    assert limiter.check("1.1.1.1", "auth", limit=1, window=60) is None


def test_retry_after_counts_down_within_window():
    clock = Clock()
    limiter = RateLimiter(time_fn=clock)
    assert limiter.check("1.1.1.1", "auth", limit=1, window=60) is None
    clock.advance(20)
    retry = limiter.check("1.1.1.1", "auth", limit=1, window=60)
    # 40s of the 60s window remain.
    assert retry == 40


def test_distinct_ips_are_limited_independently():
    clock = Clock()
    limiter = RateLimiter(time_fn=clock)
    assert limiter.check("1.1.1.1", "auth", limit=1, window=60) is None
    assert limiter.check("1.1.1.1", "auth", limit=1, window=60) is not None
    # A different source IP has its own untouched budget.
    assert limiter.check("2.2.2.2", "auth", limit=1, window=60) is None


def test_distinct_buckets_are_limited_independently():
    clock = Clock()
    limiter = RateLimiter(time_fn=clock)
    assert limiter.check("1.1.1.1", "login", limit=1, window=60) is None
    assert limiter.check("1.1.1.1", "login", limit=1, window=60) is not None
    # Same IP, different bucket -> separate budget.
    assert limiter.check("1.1.1.1", "token", limit=1, window=60) is None


# --- client-IP derivation (trusted-hops trust boundary) ---------------------


def test_client_ip_hops_zero_ignores_xff_and_uses_peer():
    # Direct exposure (default): a client-supplied XFF must NOT pick the bucket.
    req = _request(xff="70.41.3.18, 150.172.238.178", peer="10.0.0.1")
    assert client_ip(req, trusted_hops=0) == "10.0.0.1"


def test_client_ip_hops_one_uses_rightmost_entry_not_attacker_leftmost():
    # Behind one proxy: the address our proxy inserted is the rightmost entry.
    req = _request(xff="1.1.1.1, 2.2.2.2, 9.9.9.9", peer="10.0.0.1")
    assert client_ip(req, trusted_hops=1) == "9.9.9.9"


def test_client_ip_spoofed_left_entries_do_not_create_new_buckets():
    # Extra attacker-controlled left-hand entries must not change the key.
    honest = _request(xff="2.2.2.2, 9.9.9.9", peer="10.0.0.1")
    spoofed = _request(xff="8.8.8.8, 7.7.7.7, 2.2.2.2, 9.9.9.9", peer="10.0.0.1")
    assert client_ip(honest, trusted_hops=1) == client_ip(spoofed, trusted_hops=1)


def test_client_ip_short_chain_falls_back_to_peer():
    # Configured for two hops but only one entry present (spoof/misconfig):
    # fall back to the socket peer, never to a client-chosen value.
    req = _request(xff="9.9.9.9", peer="10.0.0.1")
    assert client_ip(req, trusted_hops=2) == "10.0.0.1"


def test_client_ip_non_ip_entry_falls_back_to_peer():
    # The selected XFF slot is present but not a valid IP (spoof/garbage): it
    # must not become the bucket key — fall back to the socket peer.
    req = _request(xff="not-an-ip", peer="10.0.0.1")
    assert client_ip(req, trusted_hops=1) == "10.0.0.1"


def test_client_ip_falls_back_to_socket_peer_without_xff():
    req = _request(xff=None, peer="203.0.113.9")
    assert client_ip(req, trusted_hops=1) == "203.0.113.9"


def test_client_ip_hops_one_ignores_blank_entries():
    req = _request(xff="  ,  , 9.9.9.9", peer="10.0.0.1")
    assert client_ip(req, trusted_hops=1) == "9.9.9.9"


def test_client_ip_handles_missing_peer():
    req = _request(xff=None, peer=None)
    assert client_ip(req, trusted_hops=0) == "unknown"


# --- bounded memory ---------------------------------------------------------


def test_state_is_bounded_under_distinct_ip_flood():
    clock = Clock()
    cap = 100
    limiter = RateLimiter(max_windows=cap, time_fn=clock)
    for i in range(cap * 10):
        limiter.check(f"10.0.{i // 256}.{i % 256}", "auth", limit=5, window=60)
    # An attacker rotating source IPs must NOT grow internal state without bound.
    assert limiter.tracked() <= cap


# --- concurrency ------------------------------------------------------------


def test_concurrent_checks_on_one_key_never_exceed_limit():
    # 20 threads hammer check() on a single key with a limit of 5. Under the
    # lock there must be no lost updates: exactly `limit` calls are allowed and
    # the count never exceeds it. (Makes the module docstring's thread-race
    # claim true; uses the real clock so the window never resets mid-test.)
    limiter = RateLimiter()
    limit = 5
    threads_n = 20
    allowed = 0
    allowed_lock = threading.Lock()
    start = threading.Barrier(threads_n)

    def worker() -> None:
        nonlocal allowed
        start.wait()  # maximise contention: release all threads together
        if limiter.check("9.9.9.9", "auth", limit=limit, window=60) is None:
            with allowed_lock:
                allowed += 1

    threads = [threading.Thread(target=worker) for _ in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert allowed == limit


# --- integration: real endpoints -------------------------------------------


def _app(env: dict | None = None):
    settings = Settings.load(
        {
            "TRUG_TOKEN_RING": "tok-ring",
            "TRUG_TOKEN_MCP": "tok-mcp",
            "TRUG_BOOTSTRAP_TOKEN": "boot-secret",
            "TRUG_DB_PATH": ":memory:",
            **(env or {}),
        },
        None,
    )
    return create_app(settings)


def test_bootstrap_claim_endpoint_429s_after_limit():
    from trug.ratelimit import AUTH_LIMIT

    app = _app()
    client = TestClient(app)
    headers = {"authorization": "Bearer boot-secret"}
    statuses = []
    for _ in range(AUTH_LIMIT + 1):
        r = client.post(
            "/auth/bootstrap/claim/options", json={"name": "Alice"}, headers=headers
        )
        statuses.append(r.status_code)
    assert all(s != 429 for s in statuses[:AUTH_LIMIT])
    last = client.post(
        "/auth/bootstrap/claim/options", json={"name": "Alice"}, headers=headers
    )
    assert last.status_code == 429
    assert "Retry-After" in last.headers
    assert int(last.headers["Retry-After"]) > 0
    # Generic body — must not leak whether an account/token exists.
    assert "account" not in last.text.lower()
    assert "token" not in last.text.lower()


def test_disabled_setting_never_limits():
    app = _app({"TRUG_RATE_LIMIT_ENABLED": "false"})
    client = TestClient(app)
    headers = {"authorization": "Bearer boot-secret"}
    for _ in range(30):
        r = client.post(
            "/auth/bootstrap/claim/options", json={"name": "Alice"}, headers=headers
        )
        assert r.status_code != 429


def test_token_endpoint_is_rate_limited():
    from trug.ratelimit import TOKEN_LIMIT

    app = _app()
    client = TestClient(app)
    saw_429 = False
    for _ in range(TOKEN_LIMIT + 1):
        r = client.post("/oauth/token", data={"grant_type": "refresh_token"})
        if r.status_code == 429:
            saw_429 = True
    assert saw_429


def test_login_endpoint_429s_after_limit():
    from trug.ratelimit import AUTH_LIMIT

    app = _app()
    client = TestClient(app)
    statuses = [
        client.post("/auth/login/options").status_code
        for _ in range(AUTH_LIMIT + 1)
    ]
    assert all(s != 429 for s in statuses[:AUTH_LIMIT])
    assert statuses[-1] == 429


def test_register_endpoint_429s_after_limit():
    from trug.ratelimit import AUTH_LIMIT

    app = _app()
    client = TestClient(app)
    # A well-formed but invalid invite reaches the guard on every call (400),
    # so the only 429 comes from the rate limiter, not body validation.
    statuses = [
        client.post("/auth/register/options", json={"invite": "nope"}).status_code
        for _ in range(AUTH_LIMIT + 1)
    ]
    assert all(s != 429 for s in statuses[:AUTH_LIMIT])
    assert statuses[-1] == 429


# --- config: fail-safe defaults --------------------------------------------


def test_blank_rate_limit_env_stays_enabled():
    # A bare `TRUG_RATE_LIMIT_ENABLED=` must resolve to the safe default (on),
    # never silently disable the control.
    settings = Settings.load({"TRUG_RATE_LIMIT_ENABLED": ""}, None)
    assert settings.rate_limit_enabled is True


def test_explicit_false_rate_limit_env_disables():
    settings = Settings.load({"TRUG_RATE_LIMIT_ENABLED": "false"}, None)
    assert settings.rate_limit_enabled is False


def test_typo_rate_limit_env_stays_enabled():
    # A typo like `flase` is not a recognised falsey token → stays enabled (safe).
    settings = Settings.load({"TRUG_RATE_LIMIT_ENABLED": "flase"}, None)
    assert settings.rate_limit_enabled is True


def test_trusted_proxy_hops_defaults_to_zero():
    settings = Settings.load({}, None)
    assert settings.trusted_proxy_hops == 0


def test_negative_trusted_proxy_hops_rejected():
    with pytest.raises(ValueError):
        Settings.load({"TRUG_TRUSTED_PROXY_HOPS": "-1"}, None)


def test_bootstrap_state_probe_has_its_own_bucket_and_never_starves_the_claim():
    """The gate probes /bootstrap/state on every load to decide whether to show
    the first-run onboarding. That must NOT share the claim bucket: a handful of
    page reloads would otherwise exhaust AUTH_LIMIT and 429 the very claim the
    onboarding is inviting — locking a new deployer out of their own instance."""
    from trug.ratelimit import AUTH_LIMIT

    app = _app()
    client = TestClient(app)

    # Burn well past the claim budget on the probe alone.
    for _ in range(AUTH_LIMIT * 2):
        assert client.get("/auth/bootstrap/state").status_code == 200

    # The claim is still reachable — it has its own untouched budget.
    r = client.post(
        "/auth/bootstrap/claim/options",
        json={"name": "Alice"},
        headers={"authorization": "Bearer boot-secret"},
    )
    assert r.status_code != 429


def test_bootstrap_state_probe_is_itself_bounded():
    """It is unauthenticated, so it still gets a (generous) ceiling rather than
    being a free unlimited endpoint."""
    from trug.ratelimit import PROBE_LIMIT

    client = TestClient(_app())
    statuses = [
        client.get("/auth/bootstrap/state").status_code
        for _ in range(PROBE_LIMIT + 5)
    ]
    assert all(s == 200 for s in statuses[:PROBE_LIMIT])
    assert statuses[-1] == 429
