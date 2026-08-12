from __future__ import annotations

import ipaddress
import os
import sys
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from trug.auth_repo import _MAX_OBSERVED_HOSTS, AuthRepository
from trug.bus import EventBus
from trug.config import Settings
from trug.llm import Enricher, build_client, current_config
from trug.llm_config import LLMConfigStore
from trug.oauth_repo import OAuthRepository
from trug.ratelimit import RateLimiter
from trug.repo import Repository
from trug.routes.authn import router as authn_router
from trug.routes.capture import router as capture_router
from trug.routes.catalog import router as catalog_router
from trug.routes.events import router as events_router
from trug.routes.items import router as items_router
from trug.routes.llm import router as llm_router
from trug.routes.mcp import router as mcp_router
from trug.routes.oauth import router as oauth_router


def _print_onboarding(settings: Settings) -> None:
    line = "=" * 60
    print(line)
    print("  TRUG — generated access tokens (these were NOT pinned)")
    print("  Unpinned tokens regenerate on every restart, so this banner")
    print("  reprints them each boot until you pin them. To pin, set these")
    print("  as environment variables or in config.yaml:")
    print(line)
    # Only the keys that were generated this boot are printed; pinned tokens
    # are already in the operator's env/config and are not reprinted.
    for key in settings.generated_keys:
        print(f"  TRUG_TOKEN_{key.upper()}={settings.tokens[key]}")
    print(line)
    # The banner is no longer the only way to learn the configuration: point at
    # the doctor, which shows every current token (pinned or generated) and
    # checks the silent-killer origin/RP-ID settings on demand.
    print("  Run 'docker compose exec trug trug-doctor' to check configuration")
    print("  and see current tokens.")
    print(line)


def _print_bootstrap(settings: Settings) -> None:
    """Print the generated first-user bootstrap token. Called only when it was
    generated (not pinned) AND no account exists yet — once the first account is
    claimed the token is inert, so it is never reprinted after that."""
    line = "=" * 60
    print(line)
    print("  TRUG — first-user bootstrap token (no account claimed yet)")
    print("  Open the app, choose 'use an access token', paste this, then")
    print("  create the first account. It is one-time and first-user-only:")
    print("  it stops working the moment the first account is claimed, and")
    print("  is ignored once any user exists. Pin TRUG_BOOTSTRAP_TOKEN to keep")
    print("  a stable value across restarts before the first claim.")
    print(line)
    print(f"  TRUG_BOOTSTRAP_TOKEN={settings.bootstrap_token}")
    print(line)


# Hosts that must never trip the origin-mismatch check: WebAuthn treats
# localhost as a secure context, and the container healthcheck hits /healthz as
# localhost. Recording them would make the check cry wolf on every deployment.
_IGNORED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# Don't re-write the same host to the DB more than this often (seconds). A new,
# unseen host is always recorded immediately; a busy same-origin stream is
# collapsed so the middleware stays a near-free in-memory check per request.
_HOST_FLUSH_INTERVAL = 2.0


def _observed_host(request: Request) -> str | None:
    """The bare host the request was actually reached as (X-Forwarded-Host wins
    over Host, since a tunnel/proxy carries the real external host there),
    lower-cased and stripped of any port. Returns None for the healthcheck, an
    ignored (localhost) host, or a missing/unparseable header — the cases that
    must not be recorded."""
    if request.url.path == "/healthz":
        return None
    raw = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    raw = raw.split(",")[0].strip()
    if not raw:
        return None
    # urlsplit gives correct host/port handling including IPv6 literals, but
    # raises ValueError ("Invalid IPv6 URL") on a malformed value like "[" or
    # "[::1". That is attacker-triggerable via X-Forwarded-Host, so an
    # unparseable header must be treated as "nothing to record", never a 500.
    try:
        host = (urlsplit(f"//{raw}").hostname or "").lower()
    except ValueError:
        return None
    if not host or host in _IGNORED_HOSTS:
        return None
    return host


def _peer_is_colocated_proxy(request: Request) -> bool:
    """True when the real socket peer (``request.client.host``) is a
    PRIVATE / LOOPBACK / LINK-LOCAL address — i.e. a co-located reverse proxy
    (cloudflared, Tailscale Serve, nginx/Caddy, Railway's internal ingress),
    which always reaches the app over loopback or a private network.

    This corroborates the forwarded-header proxy signal by socket peer: a
    public-internet attacker's peer is a public IP, so a forged
    ``X-Forwarded-For``/``-Host`` from them can no longer flip the write-once
    signal (which would otherwise drive ``trug-doctor`` to recommend
    TRUG_TRUSTED_PROXY_HOPS=1 and hand the attacker a rotatable rate-limit key).
    The IP parse is guarded: an absent client or an unparseable peer is treated
    as 'not a trusted proxy' (don't record)."""
    client = request.client
    if client is None:
        return False
    try:
        ip = ipaddress.ip_address(client.host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = Settings.load(os.environ, Path("config.yaml"))

    if settings.generated_tokens:
        _print_onboarding(settings)

    if settings.db_path != ":memory:":
        Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Capture the running loop so the (threadpool) items route can hand
        # enrichment coroutines back onto it.
        if app.state.enricher is not None:
            app.state.enricher.capture_loop()
        yield

    app = FastAPI(title="trug", lifespan=lifespan)
    app.state.settings = settings
    repo = Repository(settings.db_path, walk_order=settings.walk_order)
    auth_repo = AuthRepository(settings.db_path)
    # No env-seeded roster: users are created dynamically (first user via the
    # bootstrap claim, everyone else via invite). Print the bootstrap token only
    # when it was generated and the instance is still unclaimed.
    if settings.generated_bootstrap and auth_repo.user_count() == 0:
        _print_bootstrap(settings)
    oauth_repo = OAuthRepository(settings.db_path)
    bus = EventBus()
    # BYOK LLM config store: a signed-in human's provider/key/model overrides
    # the env config at runtime. TRUG_SECRET, when set, encrypts the key at rest.
    llm_store = LLMConfigStore(settings.db_path, os.environ.get("TRUG_SECRET"))
    app.state.repo = repo
    app.state.auth_repo = auth_repo
    app.state.oauth_repo = oauth_repo
    app.state.bus = bus
    app.state.llm_store = llm_store
    # In-process per-client rate limiter guarding the sensitive auth endpoints
    # (see trug/ratelimit.py). Bounded in memory; honours rate_limit_enabled.
    app.state.rate_limiter = RateLimiter()
    # A disabled security control must never be invisible: log its state at boot,
    # in the same one-line style as the rest of the bootstrap output.
    print(
        f"  TRUG — auth rate limiter {'enabled' if settings.rate_limit_enabled else 'DISABLED'}"
    )
    # Seed the client from the live config (store overriding env); reload() after
    # a config change rebuilds it in place — no redeploy.
    app.state.enricher = Enricher(
        build_client(current_config(settings, llm_store)),
        repo,
        bus,
        settings.walk_order,
        settings=settings,
        store=llm_store,
    )

    # Origin-mismatch observability: record the distinct external hosts the
    # server is actually reached as, so `trug-doctor` (a separate process that
    # shares only the DB file) can compare them to TRUG_ORIGIN and turn the
    # silent passkey/OAuth failure into a one-line diagnosis. Cheap: an
    # in-process throttle collapses a busy same-origin stream to at most one tiny
    # write every few seconds, and nothing about the request beyond its host is
    # ever touched or stored.
    # Bounded to the same cap as the persisted table: `host` derives from the
    # attacker-controllable Host / X-Forwarded-Host, so an unbounded map would
    # grow without limit AND let each new spoofed host bypass the throttle. An
    # OrderedDict evicts the least-recently-written host once the cap is hit.
    _host_last_written: OrderedDict[str, float] = OrderedDict()
    app.state.observed_host_throttle = _host_last_written
    # Last time the "proxy forwarded header seen" signal was written, so the
    # persist is throttled just like the host recording (the setter is idempotent
    # anyway, but this keeps the middleware a near-free in-memory check per
    # request). A one-element list so the closure can mutate it; -inf so the very
    # first forwarded request always writes regardless of the monotonic origin.
    _forwarded_seen_last: list[float] = [float("-inf")]

    @app.middleware("http")
    async def _observe_host(request: Request, call_next):
        host = _observed_host(request)
        if host is not None:
            now = time.monotonic()
            last = _host_last_written.get(host)
            if last is None or now - last >= _HOST_FLUSH_INTERVAL:
                _host_last_written[host] = now
                _host_last_written.move_to_end(host)
                while len(_host_last_written) > _MAX_OBSERVED_HOSTS:
                    _host_last_written.popitem(last=False)
                # record_host swallows its own storage errors (observability
                # must never break a real request), so no sqlite handling here.
                auth_repo.record_host(host)
        # Persist a one-time "behind a proxy" signal for trug-doctor when a
        # forwarded header is present — but ONLY when the real socket peer is a
        # co-located proxy (private/loopback/link-local). A public-peer forwarded
        # header is attacker-forgeable and must not flip the signal (see C1).
        # Throttled like the host recording, and mark_forwarded_header_seen
        # swallows its own storage errors so it can never 500 a request.
        if (
            "x-forwarded-for" in request.headers
            or "x-forwarded-host" in request.headers
        ) and _peer_is_colocated_proxy(request):
            now = time.monotonic()
            if now - _forwarded_seen_last[0] >= _HOST_FLUSH_INTERVAL:
                _forwarded_seen_last[0] = now
                auth_repo.mark_forwarded_header_seen()
        return await call_next(request)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    app.include_router(items_router)
    app.include_router(catalog_router)
    app.include_router(events_router)
    app.include_router(capture_router)
    app.include_router(authn_router)
    app.include_router(llm_router)
    app.include_router(mcp_router)
    # OAuth discovery well-knowns + endpoints must precede the static catch-all
    # so /.well-known/* and /oauth/* win over the SPA mount.
    app.include_router(oauth_router)

    # Serve the built PWA at the root, but only after the API routes so
    # /healthz and /api/* always win over the static catch-all.
    static_dir = Path(settings.static_dir)

    # Explicit /favicon.ico (and /favicon.png): the built PWA ships only an SVG
    # favicon, so the classic /favicon.ico path 404s. Clients (and MCP connector
    # UIs) that sniff the default favicon path get the PWA's PNG icon instead.
    _favicon_png = static_dir / "icons" / "icon-192.png"

    @app.get("/favicon.ico", include_in_schema=False)
    @app.get("/favicon.png", include_in_schema=False)
    def favicon():
        if _favicon_png.is_file():
            return FileResponse(_favicon_png, media_type="image/png")
        return Response(status_code=404)

    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app


# Module-level ASGI app for `uv run uvicorn trug.app:app`. Skipped under
# pytest so the suite's :memory: apps aren't shadowed by a real DB/onboarding.
if "pytest" not in sys.modules:
    app = create_app()
