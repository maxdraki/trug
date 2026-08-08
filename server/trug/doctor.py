"""``trug-doctor`` — a diagnostic and recovery CLI for a self-hosted Trug.

Three questions no other part of Trug answers today:

1. *Is this instance configured correctly?* — especially the identity/origin
   settings that fail **silently** (mismatched ``TRUG_RP_ID`` / ``TRUG_ORIGIN``
   break every passkey ceremony and the MCP OAuth flow with no error anywhere).
2. *What are my tokens?* — replacing "grep the logs", which stops working the
   moment a first account exists and the boot banner goes quiet.
3. *How do I get back in?* — the lockout escape hatch whose only answer today is
   hand-editing the SQLite file.

**Authorisation.** Anyone who can run this already owns the database — host
access *is* the recovery credential — so these commands take no extra secret.
That is why the tool is strictly **CLI-only and never reachable over HTTP**: it
loads config exactly as the app does (``Settings.load(os.environ,
Path("config.yaml"))``) so it can never disagree with the running server, but it
is never mounted on the FastAPI app.

Exit codes: ``0`` healthy, ``1`` warnings (works but fragile), ``2`` errors
(something is broken) — distinct so an agent driving an install can branch.
"""

from __future__ import annotations

import argparse
import ipaddress
import json as jsonlib
import os
import secrets
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from trug.auth import hash_token
from trug.auth_repo import AuthRepository
from trug.config import Settings
from trug.llm import current_config
from trug.llm_config import LLMConfigStore
from trug.oauth import authorization_server_metadata
from trug.routes.authn import _INVITE_TTL

# --- status model -----------------------------------------------------------

OK = "ok"
WARN = "warn"
FAIL = "fail"

_RANK = {OK: 0, WARN: 1, FAIL: 2}
# How a per-check status rolls up into the overall status + process exit code.
_SUMMARY = {OK: ("ok", 0), WARN: ("warn", 1), FAIL: ("error", 2)}

_REDACTED = "<redacted>"
_LOCALHOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_LOW_DISK_BYTES = 100 * 1024 * 1024  # ~100 MB


@dataclass
class Check:
    """One diagnostic line. ``id`` is a stable string an agent can match on,
    ``remedy`` (when present) carries an ``env`` map the agent can apply
    directly."""

    id: str
    status: str
    message: str
    detail: dict = field(default_factory=dict)
    remedy: dict | None = None

    def to_json(self) -> dict:
        out: dict = {
            "id": self.id,
            "status": self.status,
            "message": self.message,
            "detail": self.detail,
        }
        if self.remedy is not None:
            out["remedy"] = self.remedy
        return out


def summarise(checks: list[Check]) -> tuple[str, int]:
    """Overall ``(status, exit_code)`` — the worst check wins."""
    worst = OK
    for c in checks:
        if _RANK[c.status] > _RANK[worst]:
            worst = c.status
    return _SUMMARY[worst]


# --- small parsing helpers --------------------------------------------------


def _origin_parts(origin: str) -> tuple[str, str, int | None]:
    """``(scheme, host, port)`` for an origin, host lower-cased. Empty scheme /
    host mean it did not parse as an absolute URL."""
    parts = urlsplit(origin)
    return parts.scheme.lower(), (parts.hostname or "").lower(), parts.port


def _rp_id_issues(value: str) -> tuple[list[str], str]:
    """Return ``(issues, bare_host)`` — issues is any of ``scheme``/``path``/
    ``port`` present in an RP ID that should be a bare host."""
    issues: list[str] = []
    rest = value
    if "://" in rest:
        issues.append("scheme")
        rest = rest.split("://", 1)[1]
    if "/" in rest:
        issues.append("path")
        rest = rest.split("/", 1)[0]
    try:
        parsed = urlsplit(f"//{rest}")
        port = parsed.port
        bare = (parsed.hostname or rest).lower()
    except ValueError:
        # A bare IPv6 literal (e.g. "::1") has no brackets, so urlsplit reads the
        # trailing group as a bad port — it is a host, not a port.
        port, bare = None, rest.lower()
    if port is not None:
        issues.append("port")
    return issues, bare


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _host_matches_rp_id(host: str, rp_id: str) -> bool:
    """WebAuthn RP-ID rule: the origin host must equal the RP ID or be a
    subdomain of it (RP ID is a registrable parent)."""
    return host == rp_id or host.endswith("." + rp_id)


# --- diagnose: identity & origin -------------------------------------------


def _origin_checks(settings: Settings) -> list[Check]:
    checks: list[Check] = []
    origin = settings.origin
    scheme, host, _port = _origin_parts(origin)

    origin_ok = scheme in ("http", "https") and bool(host)
    if origin_ok:
        checks.append(
            Check(
                "origin.valid",
                OK,
                f"TRUG_ORIGIN is a valid absolute URL: {origin}",
                {"origin": origin},
            )
        )
    else:
        checks.append(
            Check(
                "origin.valid",
                FAIL,
                "TRUG_ORIGIN is not a valid absolute URL. Set it to the exact "
                "URL people load, shape https://host[:port] (e.g. "
                "https://trug.example.com).",
                {"origin": origin},
            )
        )

    rp_id = settings.rp_id
    issues, bare = _rp_id_issues(rp_id)
    if issues:
        checks.append(
            Check(
                "rp_id.bare_host",
                FAIL,
                f"TRUG_RP_ID must be a bare host (no {', '.join(issues)}). "
                f"Strip it to {bare!r}.",
                {"rp_id": rp_id, "issues": issues, "bare_host": bare},
                {"env": {"TRUG_RP_ID": bare}},
            )
        )
    else:
        checks.append(
            Check(
                "rp_id.bare_host",
                OK,
                f"TRUG_RP_ID is a bare host: {rp_id}",
                {"rp_id": rp_id},
            )
        )

    if _is_ip(bare):
        checks.append(
            Check(
                "rp_id.not_ip",
                FAIL,
                f"TRUG_RP_ID is an IP address ({bare}). WebAuthn forbids an IP "
                "as the RP ID — give the box a hostname (see the secure-context "
                "remedies) and use that.",
                {"rp_id": bare},
            )
        )
    else:
        checks.append(
            Check("rp_id.not_ip", OK, f"TRUG_RP_ID is not an IP address: {bare}", {"rp_id": bare})
        )

    # A single-label RP ID (a bare TLD like "com", or "co") is a broken config:
    # browsers reject a bare public suffix as an RP ID, so every ceremony fails.
    # localhost is the one legitimate dotless host; IPs are handled above. No
    # public-suffix-list dependency — a "must contain a dot" guard is enough.
    if not _is_ip(bare) and bare and bare != "localhost":
        if "." in bare:
            checks.append(
                Check(
                    "rp_id.registrable",
                    OK,
                    f"TRUG_RP_ID has a registrable domain shape: {bare}",
                    {"rp_id": bare},
                )
            )
        else:
            checks.append(
                Check(
                    "rp_id.registrable",
                    WARN,
                    f"TRUG_RP_ID ({bare!r}) is a single label with no dot — a bare "
                    "TLD/hostname is not a registrable domain and browsers reject "
                    "it as an RP ID. Use the full host people load (e.g. "
                    "trug.example.com).",
                    {"rp_id": bare},
                )
            )

    # Only meaningful when the origin parsed and the RP ID is a plain host.
    if origin_ok and not issues and not _is_ip(bare):
        if _host_matches_rp_id(host, bare):
            checks.append(
                Check(
                    "rp_id.matches_origin",
                    OK,
                    f"TRUG_RP_ID ({bare}) matches TRUG_ORIGIN host ({host})",
                    {"rp_id": bare, "origin_host": host},
                )
            )
        else:
            checks.append(
                Check(
                    "rp_id.matches_origin",
                    FAIL,
                    f"TRUG_RP_ID ({bare}) does not match TRUG_ORIGIN host "
                    f"({host}) and is not a registrable parent of it — every "
                    f"passkey ceremony will fail. Set TRUG_RP_ID={host}.",
                    {"rp_id": bare, "origin_host": host},
                    {"env": {"TRUG_RP_ID": host}},
                )
            )

    # Secure context: passkeys need HTTPS unless the host is localhost.
    if origin_ok and scheme == "http" and host not in _LOCALHOSTS:
        checks.append(
            Check(
                "origin.secure_context",
                FAIL,
                "Passkeys cannot work here: TRUG_ORIGIN is http:// on a "
                f"non-localhost host ({host}), which browsers treat as an "
                "insecure context. Give the box a trusted HTTPS name via one of: "
                "(1) Tailscale Serve — `tailscale serve --bg 8000`; "
                "(2) a Cloudflare Tunnel (the compose file's cloudflared block); "
                "(3) your own reverse proxy (Caddy/nginx/Traefik) terminating "
                "TLS. Then set TRUG_ORIGIN/TRUG_RP_ID to that name.",
                {"origin": origin, "scheme": scheme, "host": host},
            )
        )
    else:
        checks.append(
            Check(
                "origin.secure_context",
                OK,
                "Secure context achievable (HTTPS or localhost)",
                {"origin": origin},
            )
        )

    # OAuth discovery must agree with the configured origin. The metadata is
    # built purely from the origin string, so this never raises — but it is the
    # authoritative source, so we read the issuer from it rather than restating.
    issuer = authorization_server_metadata(origin)["issuer"]
    expected = origin.rstrip("/")
    if issuer == expected:
        checks.append(
            Check(
                "oauth.discovery_issuer",
                OK,
                "OAuth discovery issuer matches TRUG_ORIGIN",
                {"issuer": issuer},
            )
        )
    else:
        checks.append(
            Check(
                "oauth.discovery_issuer",
                WARN,
                f"OAuth discovery issuer ({issuer}) does not match TRUG_ORIGIN "
                f"({expected}) — MCP client registration will drift. Fix "
                "TRUG_ORIGIN to the real URL.",
                {"issuer": issuer, "origin": expected},
            )
        )
    return checks


def _observed_host_check(settings: Settings, observed: list[dict]) -> Check:
    """The highest-value check: compare the hosts the server was actually
    reached as (recorded by the middleware) to the configured origin, and hand
    over the exact corrected env when they disagree."""
    _scheme, configured, _port = _origin_parts(settings.origin)
    mismatches = [o for o in observed if o["host"] != configured]
    if not mismatches:
        if observed:
            return Check(
                "origin.observed_host_mismatch",
                OK,
                f"Observed host(s) match the configured origin ({configured})",
                {"configured": configured, "observed": [o["host"] for o in observed]},
            )
        return Check(
            "origin.observed_host_mismatch",
            OK,
            "No distinct external hosts observed yet (nothing to compare)",
            {"configured": configured, "observed": []},
        )
    # Suggest the most-recently-seen mismatching host (observed is last-seen
    # first). Tunnels/proxies terminate TLS, so the corrected origin is https.
    top = mismatches[0]
    host = top["host"]
    return Check(
        "origin.observed_host_mismatch",
        WARN,
        "Configured origin does not match the observed Host header "
        f"(configured {configured}, observed {host} — {top['hit_count']} "
        "request(s)). Passkeys and the MCP OAuth flow will both fail until these "
        f"agree. Fix: set TRUG_ORIGIN=https://{host} and TRUG_RP_ID={host}.",
        # detail.observed is kept a flat list of host strings — the stable agent
        # contract; per-host counts ride in the human message above.
        {"configured": configured, "observed": [o["host"] for o in mismatches]},
        {"env": {"TRUG_ORIGIN": f"https://{host}", "TRUG_RP_ID": host}},
    )


# --- diagnose: rate-limiter proxy trust -------------------------------------


def _ratelimit_checks(settings: Settings, auth_repo: AuthRepository) -> list[Check]:
    """Catch the proxy-trust misfire: the app is reached through a proxy (a
    forwarded header has been observed and persisted by the middleware) but the
    rate limiter is still keyed on the socket peer (``trusted_proxy_hops == 0``).
    Behind a tunnel/reverse proxy that peer is the proxy — identical for every
    client — so the whole internet collapses onto one bucket and a single
    attacker can exhaust it and lock the household out of login. When hops is 0
    but no forwarded header has been seen, this is a correct direct deployment and
    stays OK."""
    enabled = bool(getattr(settings, "rate_limit_enabled", True))
    hops = getattr(settings, "trusted_proxy_hops", 0)
    seen = auth_repo.forwarded_header_seen()
    detail = {
        "rate_limit_enabled": enabled,
        "trusted_proxy_hops": hops,
        "forwarded_header_seen": seen,
    }
    if enabled and hops == 0 and seen:
        return [
            Check(
                "ratelimit.proxy_trust",
                FAIL,
                "The app is behind a proxy (a forwarded header has been observed) "
                "but the rate limiter keys on the proxy's IP "
                "(TRUG_TRUSTED_PROXY_HOPS=0), so every client shares one bucket "
                "and an attacker can exhaust it to lock everyone out of login. "
                "Set TRUG_TRUSTED_PROXY_HOPS to the number of proxies in front of "
                "Trug (usually 1).",
                detail,
                {"env": {"TRUG_TRUSTED_PROXY_HOPS": "1"}},
            )
        ]
    if not enabled:
        # With the limiter off there is no bucket to collapse, so proxy trust is
        # not enforced — say exactly that rather than claim anything about the
        # forwarded header (which may well have been seen; detail carries it).
        msg = "Rate limiting is disabled (TRUG_RATE_LIMIT_ENABLED=false); proxy trust not enforced"
    elif hops == 0:
        msg = "Rate limiter keys on the socket peer (direct exposure); no proxy forwarded header seen"
    else:
        msg = f"Rate limiter trusts {hops} proxy hop(s) for the client IP"
    return [Check("ratelimit.proxy_trust", OK, msg, detail)]


# --- diagnose: tokens -------------------------------------------------------


# A GENERATED (unpinned) token is minted per process by ``Settings.load``, so the
# value THIS separate process holds does not match the running server's — it is a
# dead token. Printing it (or writing it into remedy.env → .env) would be actively
# wrong, so for a generated token we show NO value and recommend pinning instead;
# only a PINNED token (identical across processes) is safe to display.
_GENERATED_TOKEN_ADVICE = (
    "rotates on every restart and is minted per process, so this diagnostic "
    "(a separate process) cannot show the value the server is actually using. "
    "Pin {env} in .env for a stable value that survives restarts."
)


def _machine_token_checks(settings: Settings, redact: bool) -> list[Check]:
    checks: list[Check] = []
    for key in ("ring", "mcp"):
        pinned = key not in settings.generated_keys
        env_name = f"TRUG_TOKEN_{key.upper()}"
        if pinned:
            shown = _REDACTED if redact else settings.tokens.get(key, "")
            checks.append(
                Check(
                    f"token.{key}",
                    OK,
                    f"{env_name} pinned",
                    {"pinned": True, "value": shown},
                )
            )
        else:
            checks.append(
                Check(
                    f"token.{key}",
                    WARN,
                    f"{env_name} generated — " + _GENERATED_TOKEN_ADVICE.format(env=env_name),
                    {"pinned": False},
                )
            )
    return checks


def _bootstrap_token_check(settings: Settings, auth_repo: AuthRepository, redact: bool) -> Check:
    # Bootstrap: shown only while unclaimed AND pinned; once a user exists it is
    # inert, and while generated it is a per-process value we must not print.
    if auth_repo.user_count() > 0:
        return Check(
            "token.bootstrap",
            OK,
            "TRUG_BOOTSTRAP_TOKEN not shown — already claimed "
            "(see `recover` if locked out)",
            {"claimed": True},
        )
    if not settings.generated_bootstrap:
        shown = _REDACTED if redact else settings.bootstrap_token
        return Check(
            "token.bootstrap",
            OK,
            "TRUG_BOOTSTRAP_TOKEN pinned (unclaimed instance)",
            {"claimed": False, "pinned": True, "value": shown},
        )
    return Check(
        "token.bootstrap",
        WARN,
        "TRUG_BOOTSTRAP_TOKEN generated — "
        + _GENERATED_TOKEN_ADVICE.format(env="TRUG_BOOTSTRAP_TOKEN")
        + " Pin it before the first claim.",
        {"claimed": False, "pinned": False},
    )


def _token_checks(settings: Settings, auth_repo: AuthRepository, redact: bool) -> list[Check]:
    return _machine_token_checks(settings, redact) + [
        _bootstrap_token_check(settings, auth_repo, redact)
    ]


# --- diagnose: roster & lockout ---------------------------------------------


def _roster_checks(auth_repo: AuthRepository) -> list[Check]:
    checks: list[Check] = []
    users = auth_repo.list_users()
    user_count = len(users)
    enrolled = [u for u in users if u["enrolled"]]
    pending = user_count - len(enrolled)
    sessions = auth_repo.active_session_count()

    checks.append(
        Check(
            "roster.summary",
            OK,
            f"{user_count} member(s) ({len(enrolled)} enrolled, {pending} "
            f"pending), {sessions} active session(s)",
            {
                "members": user_count,
                "enrolled": len(enrolled),
                "pending": pending,
                "active_sessions": sessions,
            },
        )
    )

    # Single point of failure: exactly one enrolled member holding one
    # credential. Fires at one, never at two.
    if len(enrolled) == 1 and enrolled[0]["credential_count"] == 1:
        name = enrolled[0]["name"]
        checks.append(
            Check(
                "roster.single_point_of_failure",
                WARN,
                f"Single point of failure: {name!r} is the only enrolled member "
                "and has 1 credential. Losing that authenticator locks everyone "
                'out. Enrol a second device: Settings → Members → "+ invite '
                'someone".',
                {"member": name, "credential_count": 1},
            )
        )

    reopened = auth_repo.bootstrap_reopen_active()
    claimable = user_count == 0 or reopened
    if reopened and user_count > 0:
        # The most security-sensitive state the tool can observe: a live reopen
        # flag while members exist means the next person with the bootstrap token
        # can claim/take over. This is NOT healthy — surface it, and point at the
        # command that closes the window.
        claimable_check = Check(
            "roster.claimable",
            WARN,
            "Bootstrap re-open is LIVE while members already exist — anyone with "
            "the bootstrap token can claim an account (a takeover window left by "
            "an abandoned `recover --reset-bootstrap`). Close it now with "
            "`trug-doctor recover --cancel-reset`, or complete the intended claim.",
            {"claimable": True, "reopened": True, "members": user_count},
        )
    else:
        claimable_check = Check(
            "roster.claimable",
            OK,
            "Instance is still claimable (first-user bootstrap is open)"
            if claimable
            else "Instance is claimed (bootstrap is closed)",
            {"claimable": claimable, "reopened": reopened},
        )
    checks.append(claimable_check)

    outstanding = auth_repo.outstanding_invites()
    checks.append(
        Check(
            "roster.outstanding_invites",
            OK,
            f"{outstanding} outstanding unexpired invite(s)",
            {"outstanding_invites": outstanding},
        )
    )
    return checks


# --- diagnose: storage ------------------------------------------------------


def _storage_checks(settings: Settings, auth_repo: AuthRepository | None = None) -> list[Check]:
    checks: list[Check] = []
    db_path = settings.db_path
    if db_path == ":memory:":
        checks.append(
            Check("storage.db_path", OK, "DB is in-memory (:memory:)", {"db_path": db_path})
        )
        return checks

    path = Path(db_path)
    data_dir = path.parent
    if path.exists():
        writable = os.access(path, os.W_OK)
        if writable:
            checks.append(
                Check(
                    "storage.db_path",
                    OK,
                    f"DB exists and is writable: {path}",
                    {"db_path": str(path), "exists": True, "writable": True},
                )
            )
        else:
            checks.append(
                Check(
                    "storage.db_path",
                    FAIL,
                    f"DB exists but is not writable by uid {os.getuid()}: {path}. "
                    "Fix the file ownership/permissions on the data volume.",
                    {"db_path": str(path), "exists": True, "writable": False},
                )
            )
    else:
        dir_writable = data_dir.is_dir() and os.access(data_dir, os.W_OK)
        checks.append(
            Check(
                "storage.db_path",
                FAIL if not dir_writable else WARN,
                f"DB file does not exist yet at {path}. "
                + (
                    "Its directory is not writable — check the data volume mount "
                    "and ownership."
                    if not dir_writable
                    else "It will be created on first write."
                ),
                {"db_path": str(path), "exists": False, "dir_writable": dir_writable},
            )
        )

    # journal_mode should be WAL (concurrent reader/writer safety). Read it via
    # the repository layer (CLAUDE.md: no SQLite-isms outside the repo) — and
    # only when the file exists and we were handed a repo to read it with.
    if path.exists() and auth_repo is not None:
        mode = auth_repo.journal_mode()
        if str(mode).lower() == "wal":
            checks.append(
                Check(
                    "storage.journal_mode", OK, "PRAGMA journal_mode is wal", {"journal_mode": mode}
                )
            )
        else:
            checks.append(
                Check(
                    "storage.journal_mode",
                    WARN,
                    f"PRAGMA journal_mode is {mode!r}, not wal — concurrent access "
                    "is less safe. Trug sets WAL on connect; a non-wal mode suggests "
                    "an externally-created DB.",
                    {"journal_mode": mode},
                )
            )

    # Free space on the data volume.
    try:
        free = shutil.disk_usage(data_dir if data_dir.exists() else Path(".")).free
    except OSError:
        free = None
    if free is None:
        pass
    elif free < _LOW_DISK_BYTES:
        checks.append(
            Check(
                "storage.free_space",
                WARN,
                f"Low free space on the data volume: {free // (1024 * 1024)} MB "
                "left. Free space or grow the volume before the DB can't write.",
                {"free_bytes": free},
            )
        )
    else:
        checks.append(
            Check(
                "storage.free_space",
                OK,
                f"Free space on the data volume: {free // (1024 * 1024)} MB",
                {"free_bytes": free},
            )
        )

    # Is the data dir a mount? If not, the DB lives in the container's writable
    # layer and vanishes on `docker compose down` — the silent data-loss trap.
    try:
        is_mount = os.path.ismount(data_dir)
    except OSError:
        is_mount = False
    if is_mount:
        checks.append(
            Check(
                "storage.data_mount",
                OK,
                f"Data directory is a mount: {data_dir}",
                {"data_dir": str(data_dir), "is_mount": True},
            )
        )
    else:
        checks.append(
            Check(
                "storage.data_mount",
                WARN,
                f"Data directory ({data_dir}) is NOT a mount — the DB lives in "
                "the container's writable layer and will be LOST on `docker "
                "compose down`. Mount a volume at the data directory (compose "
                "maps ./data:/data).",
                {"data_dir": str(data_dir), "is_mount": False},
            )
        )
    return checks


# --- diagnose: enrichment ---------------------------------------------------


def _enrichment_checks(settings: Settings, llm_store: LLMConfigStore) -> list[Check]:
    checks: list[Check] = []
    cfg = current_config(settings, llm_store)
    source = cfg["source"] if cfg else "none"
    if source == "settings":
        msg = (
            "LLM enrichment config is authoritative from in-app Settings (DB) — "
            "this silently overrides any LLM_* env vars."
        )
    elif source == "env":
        msg = "LLM enrichment config is authoritative from env (LLM_*)."
    else:
        msg = "No LLM configured — enrichment uses the built-in icon map only."
    checks.append(
        Check("enrichment.llm_config", OK, msg, {"source": source})
    )

    stored = llm_store.get()
    if stored and stored.get("unreadable_key"):
        checks.append(
            Check(
                "enrichment.key_decrypt",
                WARN,
                "A stored LLM key exists but failed to decrypt (TRUG_SECRET was "
                "rotated or removed). Enrichment has silently fallen back to env. "
                "Re-enter the key in Settings → AI enrichment, or restore the "
                "original TRUG_SECRET.",
                {"unreadable_key": True},
            )
        )
    return checks


# --- diagnose orchestration -------------------------------------------------


_NOT_INITIALIZED = (
    "not checked: the database is not initialized yet — start the server once so "
    "it is created (the first request writes it), then re-run trug-doctor."
)


def _not_initialized_check(check_id: str) -> Check:
    """A DB-backed area we can't inspect because the DB isn't created yet. WARN
    (exit >=1) so an installer notices, without pretending anything is broken."""
    return Check(check_id, WARN, _NOT_INITIALIZED, {"db_initialized": False})


def diagnose(
    settings: Settings,
    auth_repo: AuthRepository | None,
    llm_store: LLMConfigStore | None,
    redact: bool = False,
) -> list[Check]:
    """Run every read-only check and return the flat list, in report order.

    ``auth_repo``/``llm_store`` are ``None`` when the database has not been
    created yet: rather than construct (and thereby CREATE) the file from a
    diagnosis, the DB-backed areas report the not-initialized state instead. The
    config-only checks (origin/RP-ID, machine tokens, storage paths) still run."""
    checks: list[Check] = []
    checks += _origin_checks(settings)
    if auth_repo is None:
        checks += _machine_token_checks(settings, redact)
        checks.append(_not_initialized_check("origin.observed_host_mismatch"))
        checks.append(_not_initialized_check("token.bootstrap"))
        checks.append(_not_initialized_check("roster.summary"))
        checks += _storage_checks(settings, None)
        checks.append(_not_initialized_check("enrichment.llm_config"))
        return checks
    checks.append(_observed_host_check(settings, auth_repo.observed_hosts()))
    checks += _ratelimit_checks(settings, auth_repo)
    checks += _token_checks(settings, auth_repo, redact)
    checks += _roster_checks(auth_repo)
    checks += _storage_checks(settings, auth_repo)
    checks += _enrichment_checks(settings, llm_store)
    return checks


# --- rendering --------------------------------------------------------------

_PREFIX = {OK: "OK  ", WARN: "WARN", FAIL: "FAIL"}


def render_text(checks: list[Check], out) -> None:
    status, _ = summarise(checks)
    for c in checks:
        first, *rest = c.message.split(". ")
        print(f"{_PREFIX[c.status]}  {first}", file=out)
        # Wrap the remainder of the message onto indented continuation lines.
        for extra in rest:
            extra = extra.strip()
            if extra:
                print(f"      {extra}", file=out)
        if c.remedy and "env" in c.remedy:
            for k, v in c.remedy["env"].items():
                print(f"      → {k}={v}", file=out)
    print(file=out)
    print(f"status: {status}", file=out)


def render_json(checks: list[Check], out) -> None:
    status, exit_code = summarise(checks)
    payload = {
        "status": status,
        "exit_code": exit_code,
        "checks": [c.to_json() for c in checks],
    }
    print(jsonlib.dumps(payload, indent=2), file=out)


# --- recovery ---------------------------------------------------------------


def _origin_looks_wrong(settings: Settings, auth_repo: AuthRepository) -> Check | None:
    """The origin/observed checks that would make a minted link land on the
    wrong host, or None if the origin looks usable."""
    for c in _origin_checks(settings):
        if c.id in ("origin.valid", "rp_id.matches_origin", "origin.secure_context") and c.status == FAIL:
            return c
    obs = _observed_host_check(settings, auth_repo.observed_hosts())
    if obs.status == WARN:
        return obs
    return None


def recover_invite(
    settings: Settings, auth_repo: AuthRepository, name: str, out, redact: bool = False
) -> dict:
    """Mint an invite link for ``name`` via the exact same path the app uses —
    the primary escape hatch: no session needed, all data preserved."""
    name = name.strip()
    if not name:
        print("A name is required: recover --invite NAME", file=out)
        return {"ok": False, "reason": "no_name"}
    target = auth_repo.get_user_by_name(name)
    if target is None:
        target = auth_repo.create_user(name)
    token = secrets.token_urlsafe(32)
    auth_repo.create_invite(hash_token(token), target["id"], _INVITE_TTL)
    origin = settings.origin.rstrip("/")
    url = f"{origin}/#invite={token}"
    hours = _INVITE_TTL // 3600
    print(f"Invite for {name!r} (valid {hours}h, single use):", file=out)
    print(f"  {_REDACTED if redact else url}", file=out)
    wrong = _origin_looks_wrong(settings, auth_repo)
    if wrong is not None:
        print(
            "  WARNING: the origin looks wrong, so this link may be on the wrong "
            f"host. {wrong.message}",
            file=out,
        )
    return {"ok": True, "name": name, "token": token, "url": url}


_RESET_CONFIRM = "reset-bootstrap"


def recover_reset_bootstrap(
    settings: Settings,
    auth_repo: AuthRepository,
    out,
    force: bool = False,
    yes: bool = False,
    input_func: Callable[[str], str] = input,
    redact: bool = False,
) -> dict:
    """Re-open the one-time first-user claim, non-destructively. Refuses while
    any enrolled credential exists unless ``--force``; ``--force`` prints exactly
    what it will do and requires typed confirmation unless ``--yes``."""
    enrolled = auth_repo.enrolled_count()
    if enrolled > 0 and not force:
        print(
            f"Refusing: {enrolled} enrolled credential(s) still exist. Someone "
            "can still sign in, so `recover --invite NAME` is the better answer "
            "(it preserves data and needs no confirmation). Re-run with --force "
            "to reopen the claim anyway.",
            file=out,
        )
        return {"ok": False, "reopened": False, "reason": "enrolled_exist"}

    if enrolled > 0 and force:
        print(
            "--force will RE-OPEN the first-user bootstrap claim even though "
            f"{enrolled} enrolled credential(s) exist. The next person to run the "
            "claim ceremony with the bootstrap token becomes an enrolled member "
            "(attaching to an existing same-named user, or creating a new one). "
            "No existing user, credential or data is deleted.",
            file=out,
        )
        if not yes:
            answer = input_func(f"Type '{_RESET_CONFIRM}' to confirm: ").strip()
            if answer != _RESET_CONFIRM:
                print("Aborted — nothing changed.", file=out)
                return {"ok": False, "reopened": False, "reason": "not_confirmed"}

    auth_repo.reopen_bootstrap()
    shown = _REDACTED if redact else settings.bootstrap_token
    print("Bootstrap claim re-opened. Claim the first account now:", file=out)
    print(
        "  Open the app, choose 'use an access token', paste the bootstrap "
        "token, then create the account. The claim closes bootstrap again on "
        "success.",
        file=out,
    )
    print(f"  TRUG_BOOTSTRAP_TOKEN={shown}", file=out)
    if settings.generated_bootstrap:
        print(
            "  NOTE: this bootstrap token is generated, not pinned — it only "
            "matches the RUNNING server if TRUG_BOOTSTRAP_TOKEN is pinned in the "
            "env. If it is not, use the value from the server's boot logs, or pin "
            "TRUG_BOOTSTRAP_TOKEN and restart before claiming.",
            file=out,
        )
    return {
        "ok": True,
        "reopened": True,
        "bootstrap_token": settings.bootstrap_token,
    }


def recover_revoke_sessions(auth_repo: AuthRepository, out) -> dict:
    """Invalidate every session (the stolen-device answer). Passkeys stay
    enrolled, so everyone simply signs in again."""
    n = auth_repo.revoke_all_sessions()
    print(
        f"Revoked {n} active session(s). Everyone must sign in again with their "
        "passkey; enrolled credentials are untouched.",
        file=out,
    )
    return {"ok": True, "revoked": n}


def recover_cancel_reset(auth_repo: AuthRepository, out) -> dict:
    """Close a bootstrap re-open window left live by an aborted/abandoned
    ``recover --reset-bootstrap``. Without this the instance stays re-claimable
    indefinitely — anyone with the bootstrap token could take over. Clearing the
    flag restores the one-time property; no user, credential or session is touched."""
    was_active = auth_repo.bootstrap_reopen_active()
    auth_repo.clear_bootstrap_reopen()
    if was_active:
        print(
            "Bootstrap re-open window CLOSED. The instance is no longer "
            "re-claimable; the bootstrap token is inert again while members exist.",
            file=out,
        )
    else:
        print("No bootstrap re-open was active — nothing to close.", file=out)
    return {"ok": True, "cancelled": was_active}


def _print_recover_options(out) -> None:
    print("recover — mutating recovery actions (host access is the credential):", file=out)
    print(file=out)
    print("  --invite NAME        Mint an invite link (the primary escape hatch:", file=out)
    print("                       no session needed, preserves all data).", file=out)
    print("  --reset-bootstrap    Re-open the one-time first-user claim,", file=out)
    print("                       non-destructively. Refuses while an enrolled", file=out)
    print("                       credential exists unless --force.", file=out)
    print("  --cancel-reset       Close a bootstrap re-open window left live by", file=out)
    print("                       an aborted --reset-bootstrap (restores one-time).", file=out)
    print("  --revoke-sessions    Invalidate all sessions (stolen-device answer);", file=out)
    print("                       passkeys stay enrolled.", file=out)
    print(file=out)
    print("  --force / --yes      Override / skip typed confirmation for", file=out)
    print("                       --reset-bootstrap.", file=out)
    print(file=out)
    print("Bare `recover` changes nothing. User deletion is out of scope.", file=out)


# --- entry point ------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trug-doctor",
        description=(
            "Diagnose a Trug instance's configuration (read-only, default) and "
            "recover from lockouts. CLI-only — host access is the recovery "
            "credential, so no extra secret is needed."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="machine-readable output for agents (flat, stable check ids + remedy.env).",
    )
    parser.add_argument(
        "--redact",
        action="store_true",
        help=(
            "redact token/link values so the output is safe to paste into an "
            "issue. Values are shown by default because the caller already has "
            "host access."
        ),
    )
    sub = parser.add_subparsers(dest="command")
    rec = sub.add_parser("recover", help="mutating recovery actions (bare = list options)")
    rec.add_argument("--invite", metavar="NAME", help="mint an invite link for NAME")
    rec.add_argument(
        "--reset-bootstrap",
        action="store_true",
        help="re-open the one-time first-user claim (non-destructive)",
    )
    rec.add_argument(
        "--cancel-reset",
        action="store_true",
        help="close a bootstrap re-open window left live by an aborted --reset-bootstrap",
    )
    rec.add_argument(
        "--revoke-sessions",
        action="store_true",
        help="invalidate all sessions (passkeys stay enrolled)",
    )
    rec.add_argument(
        "--force",
        action="store_true",
        help="reopen bootstrap even with enrolled credentials (needs confirmation)",
    )
    rec.add_argument(
        "--yes",
        action="store_true",
        help="skip the typed confirmation for --force",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    out = sys.stdout

    settings = Settings.load(os.environ, Path("config.yaml"))

    if args.command == "recover":
        # Recovery actions legitimately WRITE, so they get a normal (creating)
        # repo — the operator asked to change state.
        auth_repo = AuthRepository(settings.db_path)
        actions = [
            bool(args.invite),
            args.reset_bootstrap,
            args.cancel_reset,
            args.revoke_sessions,
        ]
        if sum(actions) == 0:
            _print_recover_options(out)
            return 0
        if sum(actions) > 1:
            print("Choose exactly one recover action at a time.", file=out)
            return 1
        if args.invite:
            result = recover_invite(settings, auth_repo, args.invite, out, redact=args.redact)
        elif args.reset_bootstrap:
            result = recover_reset_bootstrap(
                settings,
                auth_repo,
                out,
                force=args.force,
                yes=args.yes,
                redact=args.redact,
            )
        elif args.cancel_reset:
            result = recover_cancel_reset(auth_repo, out)
        else:
            result = recover_revoke_sessions(auth_repo, out)
        return 0 if result.get("ok") else 1

    # Default: diagnose — strictly READ-ONLY. Never create or migrate the DB from
    # a diagnosis (running as root before the server's first boot would otherwise
    # leave a root-owned file the uid-999 server then can't open). If the DB file
    # is absent, report the not-initialized state instead of constructing a
    # writing repo; if present, open it read-only so nothing is mutated.
    db_path = settings.db_path
    if db_path != ":memory:" and not Path(db_path).exists():
        auth_repo = None
        llm_store = None
    else:
        auth_repo = AuthRepository(db_path, read_only=db_path != ":memory:")
        llm_store = LLMConfigStore(db_path, os.environ.get("TRUG_SECRET"))
    checks = diagnose(settings, auth_repo, llm_store, redact=args.redact)
    if args.json:
        render_json(checks, out)
    else:
        render_text(checks, out)
    _status, exit_code = summarise(checks)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
