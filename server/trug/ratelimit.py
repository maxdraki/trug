"""A small, dependency-free, in-process per-client rate limiter.

Defense-in-depth for an internet-exposed app that is its own OAuth 2.1 AS +
passkey authenticator. It is deliberately NOT a substitute for a real reverse
proxy limiter (Cloudflare, nginx, Tailscale) — it's the belt to their braces,
so a direct-exposure deployment still gets basic brute-force resistance on the
auth ceremonies.

Design:

* **Fixed window.** For each (client_ip, bucket) we track a window start and a
  count. Simpler than a token bucket and entirely sufficient here — the goal is
  to blunt automated brute force, not to shape traffic precisely.
* **Bounded memory.** State lives in a capped ``OrderedDict``; once the cap is
  hit the least-recently-seen window is evicted. An attacker rotating source IPs
  therefore CANNOT grow it without bound (this mirrors the fix already applied to
  the observed-host throttle in app.py).
* **Injectable clock.** ``time_fn`` defaults to ``time.monotonic`` but tests pass
  a hand-cranked clock, so window behaviour is deterministic with no real sleep.
* **Thread-safe.** A single lock guards the map; the ceremony routes are hit
  concurrently (and the suite exercises genuine thread races).

Client IP is derived through an explicit trust boundary, ``TRUG_TRUSTED_PROXY_HOPS``
(see :func:`client_ip`). With the default of 0 hops (directly exposed) the
socket peer is used and ``X-Forwarded-For`` is ignored entirely — a client cannot
rotate its apparent IP by forging a header. Behind ``N`` trusted proxies the
Nth-from-the-right XFF entry (the address our own proxy layer inserted) is used,
never the client-controlled leftmost value. A proxied deployment MUST set the
hops so real client IPs, not the shared proxy peer, drive the buckets.
"""

from __future__ import annotations

import ipaddress
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from math import ceil

from fastapi import Depends, HTTPException, Request

# --- tunable limits ---------------------------------------------------------
# All limits are "requests per window seconds", per client IP, per bucket.
# Kept as module constants so they are trivial to tune in one place.

# The auth ceremonies (bootstrap claim, login, register/invite redemption).
# Deliberately tight: a human doing a passkey ceremony makes a handful of
# requests, never dozens.
AUTH_LIMIT = 10
AUTH_WINDOW = 60

# The OAuth token endpoint runs a bit hotter — a single client legitimately
# exchanges a code and then rotates refresh tokens — so it gets more headroom.
TOKEN_LIMIT = 30
TOKEN_WINDOW = 60

# Unauthenticated read-only probes (currently the gate's claimable check). The
# gate hits this on every load, so the ceiling is generous — its own bucket
# keeps ordinary page reloads from eating the far tighter claim budget.
PROBE_LIMIT = 60
PROBE_WINDOW = 60

# Hard cap on the number of distinct (ip, bucket) windows held in memory. An
# attacker rotating source IPs cannot grow state past this: the least-recently
# seen window is evicted once the cap is reached.
MAX_TRACKED_WINDOWS = 10_000


class RateLimiter:
    """Fixed-window limiter keyed by (client_ip, bucket), bounded and thread-safe."""

    def __init__(
        self,
        *,
        max_windows: int = MAX_TRACKED_WINDOWS,
        time_fn: Callable[[], float] = time.monotonic,
    ):
        # key -> (window_start, count). OrderedDict gives us LRU eviction.
        self._windows: OrderedDict[tuple[str, str], tuple[float, int]] = OrderedDict()
        self._max = max_windows
        self._time = time_fn
        self._lock = threading.Lock()

    def check(self, ip: str, bucket: str, limit: int, window: int) -> int | None:
        """Record one hit for (ip, bucket).

        Returns ``None`` when the request is within the limit, otherwise the
        integer number of seconds until the current window resets (the value for
        a ``Retry-After`` header). Allowed calls count toward the window; once the
        limit is reached, further calls are rejected until the fixed window elapses
        (denied calls do not extend it — this is a plain fixed window).
        """
        now = self._time()
        key = (ip, bucket)
        with self._lock:
            entry = self._windows.get(key)
            if entry is None or now - entry[0] >= window:
                # Fresh window (new key, or the previous one has elapsed).
                self._windows[key] = (now, 1)
                self._windows.move_to_end(key)
                self._evict()
                return None

            start, count = entry
            # Keep active keys hot so an attacker's window can't be evicted out
            # from under the limit by other traffic.
            self._windows.move_to_end(key)
            if count < limit:
                self._windows[key] = (start, count + 1)
                return None
            # Over the limit: report time remaining, at least 1 second.
            return max(1, ceil(window - (now - start)))

    def _evict(self) -> None:
        while len(self._windows) > self._max:
            self._windows.popitem(last=False)

    def tracked(self) -> int:
        """Number of windows currently held — for tests/introspection."""
        with self._lock:
            return len(self._windows)


def client_ip(request: Request, trusted_hops: int = 0) -> str:
    """The caller's IP, resolved through an explicit trust boundary.

    ``trusted_hops`` is the number of trusted reverse proxies in front of the app
    (``TRUG_TRUSTED_PROXY_HOPS``):

    * ``0`` (default, direct exposure): ``X-Forwarded-For`` is IGNORED entirely and
      the socket peer is used. A client cannot forge a header to rotate buckets.
    * ``N >= 1``: the client IP is the Nth-from-the-right XFF entry (``parts[-N]``)
      — the address our own proxy layer inserted, not the client-chosen leftmost
      one. If the chain is shorter than ``N`` (spoofing or misconfig), fall back to
      the socket peer rather than trust a client-supplied value; likewise if the
      selected entry does not parse as an IP address (garbage/spoof), fall back to
      the peer rather than key a bucket on an attacker-chosen token.

    Whitespace is trimmed and empty entries ignored. If no usable IP is found the
    socket peer is used; only if that too is absent, ``"unknown"``.
    """
    peer = request.client.host if request.client else None
    if trusted_hops > 0:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            # Only index into the chain when it is at least as long as the
            # configured hops — otherwise parts[-hops] would reach past the real
            # chain into an attacker-controlled leftmost entry.
            if len(parts) >= trusted_hops:
                # I3 (accepted limitation): the operator MUST set trusted_hops to
                # the exact number of proxies in front of Trug. Over-setting it
                # makes parts[-trusted_hops] reach past the real (proxy-inserted)
                # chain into a client-controlled XFF entry — IP-validation below
                # doesn't help, since an attacker can supply a well-formed IP. The
                # short-chain guard above only defends the under-length case.
                candidate = parts[-trusted_hops]
                try:
                    ipaddress.ip_address(candidate)
                except ValueError:
                    pass  # not a real IP → fall through to the socket peer
                else:
                    return candidate
    return peer if peer else "unknown"


def rate_limit(bucket: str, limit: int, window: int):
    """Build a FastAPI dependency enforcing ``limit`` per ``window`` for ``bucket``.

    Honours the ``rate_limit_enabled`` setting (default on) so localhost dev,
    tests, and proxy-fronted deployments can opt out cleanly. On limit exceeded
    it raises 429 with a ``Retry-After`` header and a deliberately generic JSON
    body that reveals nothing about accounts or tokens.
    """

    def dependency(request: Request) -> None:
        settings = request.app.state.settings
        if not getattr(settings, "rate_limit_enabled", True):
            return
        limiter: RateLimiter = request.app.state.rate_limiter
        hops = getattr(settings, "trusted_proxy_hops", 0)
        retry_after = limiter.check(
            client_ip(request, hops), bucket, limit, window
        )
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail="Too many requests",
                headers={"Retry-After": str(retry_after)},
            )

    return Depends(dependency)


# Pre-built guards for the sensitive endpoints. Import and drop into a route's
# ``dependencies=[...]`` list.
bootstrap_rate_limit = rate_limit("bootstrap", AUTH_LIMIT, AUTH_WINDOW)
login_rate_limit = rate_limit("login", AUTH_LIMIT, AUTH_WINDOW)
register_rate_limit = rate_limit("register", AUTH_LIMIT, AUTH_WINDOW)
token_rate_limit = rate_limit("token", TOKEN_LIMIT, TOKEN_WINDOW)
# RFC 7591 Dynamic Client Registration (/oauth/register): an unauthenticated
# write, so it gets its own tight bucket (same shape as the auth ceremonies).
dcr_rate_limit = rate_limit("dcr", AUTH_LIMIT, AUTH_WINDOW)
# Deliberately a SEPARATE bucket from ``bootstrap_rate_limit``: the gate polls
# the claimable probe on every load, and sharing would let a few page reloads
# 429 the claim itself — locking a new deployer out of their own instance.
bootstrap_state_rate_limit = rate_limit("bootstrap_state", PROBE_LIMIT, PROBE_WINDOW)
