"""Core logic for Trug's in-process OAuth 2.1 + CIMD authorization server.

Household-scale, self-contained, no external IdP: Trug is both the resource
server (``/mcp``) and its own authorization server. This module holds the pure
pieces — PKCE, opaque-token minting, client resolution (CIMD + DCR), redirect
matching, the discovery-metadata documents, the consent page, and access-token
verification. The HTTP wiring lives in ``trug/routes/oauth.py``; storage in
``trug/oauth_repo.py``.

Spec: MCP authorization revision 2026-07-28 — CIMD (client-id-metadata
documents) replaces deprecated DCR, RFC 9207 ``iss`` on authorization
responses, RFC 8707 resource indicators binding tokens to ``/mcp``. Claude
selects CIMD only when the AS metadata advertises BOTH
``client_id_metadata_document_supported: true`` AND ``"none"`` in
``token_endpoint_auth_methods_supported`` (its CIMD client is a public client);
otherwise it falls back to DCR — so we advertise both and implement a minimal
deprecated DCR ``registration_endpoint`` as the fallback path.
"""

from __future__ import annotations

import base64
import hashlib
import html
import ipaddress
import json
import logging
import secrets
import socket
from urllib.parse import urlparse, urlunparse

import httpx

from trug.auth import hash_token

logger = logging.getLogger("trug.oauth")

# Lifetimes.
CODE_TTL = 300  # authorization code: single-use, 5 min
ACCESS_TTL = 3600  # access token: ~1h
REFRESH_TTL = 90 * 24 * 3600  # refresh token: ~90d, rotating
CIMD_CACHE_TTL = 300  # cache a fetched CIMD document briefly (5 min)

# SSRF-hardening budget for the CIMD fetch (see fetch_cimd_document).
CIMD_TIMEOUT = 3.0  # seconds, per phase — total fetch bounded well under this
CIMD_MAX_BYTES = 64 * 1024  # response-body cap; larger bodies are rejected
CIMD_ALLOWED_PORTS = (443,)  # https default only

DEFAULT_SCOPE = "mcp"
SCOPES_SUPPORTED = ["mcp", "offline_access"]

# Claude's own well-known CIMD clients. Documented at
# https://claude.com/docs/connectors/building/authentication — the hosted
# surfaces (web/Desktop/mobile/Cowork) share one client id + the fixed callback;
# Claude Code is a native client using RFC 8252 loopback redirects on an
# ephemeral port. We seed these so the flow works even if the metadata URL is
# transiently unreachable, and so tests need no network.
KNOWN_CLIENTS: dict[str, dict] = {
    "https://claude.ai/oauth/mcp-oauth-client-metadata": {
        "client_name": "Claude",
        "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
    },
    "https://claude.ai/oauth/claude-code-client-metadata": {
        "client_name": "Claude Code",
        "redirect_uris": ["http://localhost/callback", "http://127.0.0.1/callback"],
    },
}


# --- opaque tokens ---------------------------------------------------------


def new_token() -> str:
    """A fresh opaque secret (URL-safe). Callers store only ``hash_token`` of
    it; the raw value is returned to the client once and never persisted."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hash_token(token)


# --- PKCE (RFC 7636, S256) -------------------------------------------------


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def compute_s256_challenge(verifier: str) -> str:
    return _b64url_no_pad(hashlib.sha256(verifier.encode("ascii")).digest())


def verify_pkce(verifier: str, challenge: str, method: str) -> bool:
    """S256 only (the sole method we advertise / the spec requires). A plain
    method or a mismatch fails closed."""
    if method != "S256" or not verifier:
        return False
    return secrets.compare_digest(compute_s256_challenge(verifier), challenge)


# --- redirect-uri matching -------------------------------------------------


def _is_loopback(uri: str) -> bool:
    host = urlparse(uri).hostname
    return host in ("localhost", "127.0.0.1", "::1")


def redirect_uri_allowed(registered: list[str], requested: str) -> bool:
    """Exact match, except loopback redirects (RFC 8252 native clients, e.g.
    Claude Code) where the port is ignored — the client binds an ephemeral port
    per session — but scheme, host and path must still match a registered
    loopback entry."""
    if requested in registered:
        return True
    if not _is_loopback(requested):
        return False
    req = urlparse(requested)
    for entry in registered:
        if not _is_loopback(entry):
            continue
        reg = urlparse(entry)
        if (
            req.scheme == reg.scheme
            and req.hostname == reg.hostname
            and req.path == reg.path
        ):
            return True
    return False


# --- client resolution (CIMD + known + DCR) --------------------------------

# --- SSRF-hardened CIMD fetch ---------------------------------------------
#
# The client_id is an attacker-suppliable URL and this is an internet-facing
# authorization server, so fetching it is a textbook SSRF sink. Defenses, in
# order: https-only + port-443-only, no raw-IP / localhost hosts, a DNS-resolve
# guard that rejects any address in a non-public range, and — crucially — we
# CONNECT TO THE VETTED IP rather than re-resolving the hostname, so a name that
# resolved public at check time cannot be rebound to an internal address before
# the socket opens (DNS-rebinding TOCTOU). Redirects are disabled, the body is
# size-capped and must be application/json. Every rejection is quiet to the
# caller and logged as a structured warning without any response body.


def _resolve_ips(hostname: str) -> list[str]:
    """Resolve ``hostname`` to every A/AAAA address. Monkeypatchable seam so
    tests drive the IP guard without real DNS. Empty list on failure."""
    try:
        infos = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    # Dedupe while preserving order; sockaddr[0] is the numeric address.
    seen: dict[str, None] = {}
    for info in infos:
        seen.setdefault(info[4][0], None)
    return list(seen)


def _ip_is_blocked(addr: str) -> bool:
    """True if ``addr`` is in any non-public range we must never connect to:
    private, loopback, link-local, multicast, reserved or unspecified. Covers
    IPv4, IPv6, and IPv4-mapped IPv6 (e.g. ``::ffff:10.0.0.1``) by also
    evaluating the embedded IPv4 address."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True  # unparseable → fail closed
    candidates = [ip]
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        candidates.append(mapped)
    return any(
        c.is_private
        or c.is_loopback
        or c.is_link_local
        or c.is_multicast
        or c.is_reserved
        or c.is_unspecified
        for c in candidates
    )


def _fetch_pinned(url: str, ip: str, hostname: str) -> dict | None:
    """Perform the actual HTTPS GET pinned to the already-vetted ``ip``.

    Monkeypatchable seam. We connect to ``ip`` directly (never re-resolving
    ``hostname``) but keep the original hostname as both the TLS SNI and the
    ``Host`` header, so certificate validation and virtual-host routing stay
    correct — this is the DNS-rebinding defense. Redirects are disabled; the
    body is streamed and cut at ``CIMD_MAX_BYTES``. Returns a small dict
    ``{status_code, content_type, body, truncated}`` or None on transport error.
    """
    parsed = urlparse(url)
    port = parsed.port or 443
    host_in_url = f"[{ip}]" if ":" in ip else ip
    pinned = urlunparse((
        parsed.scheme,
        f"{host_in_url}:{port}",
        parsed.path or "/",
        parsed.params,
        parsed.query,
        "",
    ))
    try:
        client = httpx.Client(
            timeout=httpx.Timeout(CIMD_TIMEOUT),
            follow_redirects=False,
        )
        with client, client.stream(
            "GET",
            pinned,
            headers={"Host": hostname, "Accept": "application/json"},
            extensions={"sni_hostname": hostname},
        ) as resp:
            body = bytearray()
            truncated = False
            for chunk in resp.iter_bytes():
                body.extend(chunk)
                if len(body) > CIMD_MAX_BYTES:
                    truncated = True
                    break
            return {
                "status_code": resp.status_code,
                "content_type": resp.headers.get("content-type"),
                "body": bytes(body[:CIMD_MAX_BYTES]),
                "truncated": truncated,
            }
    except httpx.HTTPError:
        return None


def _reject(client_id: str, reason: str) -> None:
    """Structured server-side warning for a rejected CIMD fetch. Logs the
    reason and the target host only — never any response body."""
    host = urlparse(client_id).hostname or ""
    logger.warning("cimd fetch rejected: reason=%s host=%s", reason, host)


# Seam so tests can drive CIMD resolution without a network fetch.
def fetch_cimd_document(client_id: str) -> dict | None:
    """Fetch a Client ID Metadata Document from its HTTPS URL (the client_id
    itself, per CIMD), with full SSRF hardening. Returns the parsed JSON dict on
    success, or None on any rejection (quiet to the caller; a structured warning
    is logged server-side)."""
    parsed = urlparse(client_id)

    # Scheme: https only. No exception — seeded known clients never reach here.
    if parsed.scheme != "https":
        _reject(client_id, "scheme")
        return None

    hostname = parsed.hostname
    if not hostname:
        _reject(client_id, "no_host")
        return None

    # No literal localhost, and no raw-IP client_ids (both bypass the DNS guard
    # and are never legitimate CIMD URLs).
    if hostname.lower() == "localhost":
        _reject(client_id, "localhost")
        return None
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass  # a real hostname, as required
    else:
        _reject(client_id, "raw_ip")
        return None

    # Port: 443 only (implicit or explicit).
    port = parsed.port
    if port is not None and port not in CIMD_ALLOWED_PORTS:
        _reject(client_id, "port")
        return None

    # Resolve, then reject if ANY resolved address is non-public.
    ips = _resolve_ips(hostname)
    if not ips:
        _reject(client_id, "unresolvable")
        return None
    if any(_ip_is_blocked(addr) for addr in ips):
        _reject(client_id, "blocked_ip")
        return None

    # Connect to the vetted IP (not a re-resolved hostname) — DNS-rebinding
    # defense. Any resolved address is public here; pin to the first.
    result = _fetch_pinned(client_id, ips[0], hostname)
    if result is None:
        _reject(client_id, "transport")
        return None
    if result["status_code"] != 200:
        # A redirect (3xx) or any non-200 is a rejection.
        _reject(client_id, "status")
        return None
    if result["truncated"]:
        _reject(client_id, "oversize")
        return None
    ctype = (result["content_type"] or "").split(";", 1)[0].strip().lower()
    if ctype != "application/json":
        _reject(client_id, "content_type")
        return None
    try:
        doc = json.loads(result["body"])
    except (ValueError, UnicodeDecodeError):
        _reject(client_id, "parse")
        return None
    if not isinstance(doc, dict):
        _reject(client_id, "not_object")
        return None
    return doc


def resolve_client(client_id: str, oauth_repo) -> dict | None:
    """Resolve ``client_id`` to ``{client_id, client_name, redirect_uris}``.

    Order: a DCR-registered client (stored locally) → a known Claude CIMD
    client (seeded) → a CIMD document fetched from the client_id URL (cached
    briefly). Returns None if the id is neither a registered client nor a
    resolvable HTTPS CIMD URL.
    """
    registered = oauth_repo.get_client(client_id)
    if registered is not None:
        return {
            "client_id": client_id,
            "client_name": registered.get("client_name"),
            "redirect_uris": registered["redirect_uris"],
        }

    if client_id in KNOWN_CLIENTS:
        known = KNOWN_CLIENTS[client_id]
        return {"client_id": client_id, **known}

    # CIMD: the client_id must be an HTTPS URL we can fetch.
    if not client_id.lower().startswith("https://"):
        return None

    cached = oauth_repo.get_cached_cimd(client_id)
    if cached is not None:
        return {
            "client_id": client_id,
            "client_name": cached.get("client_name"),
            "redirect_uris": cached["redirect_uris"],
        }

    doc = fetch_cimd_document(client_id)
    if not isinstance(doc, dict):
        return None
    # Per CIMD, the document's own client_id (if present) must equal the URL.
    if doc.get("client_id") not in (None, client_id):
        return None
    redirect_uris = doc.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return None
    client_name = doc.get("client_name")
    oauth_repo.cache_cimd(client_id, client_name, redirect_uris, CIMD_CACHE_TTL)
    return {
        "client_id": client_id,
        "client_name": client_name,
        "redirect_uris": redirect_uris,
    }


# --- discovery metadata ----------------------------------------------------


def mcp_resource(origin: str) -> str:
    """The canonical resource identifier for the MCP endpoint — what Claude
    sends as the RFC 8707 ``resource`` and what tokens are bound to."""
    return origin.rstrip("/") + "/mcp"


def protected_resource_metadata(origin: str) -> dict:
    """RFC 9728 protected-resource metadata. Advertises the same-origin
    authorization server so Claude can discover the flow from a ``/mcp`` 401."""
    origin = origin.rstrip("/")
    return {
        "resource": mcp_resource(origin),
        "authorization_servers": [origin],
        "scopes_supported": SCOPES_SUPPORTED,
        "bearer_methods_supported": ["header"],
    }


def authorization_server_metadata(origin: str) -> dict:
    """RFC 8414 authorization-server metadata. Advertises CIMD (both signals
    Claude requires: ``client_id_metadata_document_supported`` AND ``none`` in
    ``token_endpoint_auth_methods_supported``) plus a deprecated DCR
    ``registration_endpoint`` fallback."""
    origin = origin.rstrip("/")
    return {
        "issuer": origin,
        "authorization_endpoint": f"{origin}/oauth/authorize",
        "token_endpoint": f"{origin}/oauth/token",
        "registration_endpoint": f"{origin}/oauth/register",
        "scopes_supported": SCOPES_SUPPORTED,
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": True,
        "resource_indicators_supported": True,
    }


# --- consent page ----------------------------------------------------------


def consent_page(client_name: str | None, user_name: str, action: str,
                 hidden: dict[str, str], redirect_host: str) -> str:
    """Minimal server-rendered consent page in the DESIGN.md voice: plain,
    lowercase-leaning, zero filler. Names the client and the redirect host (the
    spec requires showing the redirect hostname clearly, especially for loopback
    clients). Approve/deny post back the flow params in hidden fields."""
    name = html.escape(client_name or "an app")
    who = html.escape(user_name)
    host = html.escape(redirect_host)
    fields = "".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
        for k, v in hidden.items()
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>trug — authorize</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    background: #11111b; color: #cdd6f4;
    font-family: Inter, system-ui, -apple-system, sans-serif;
  }}
  main {{ width: min(92vw, 400px); background: #1e1e2e; border: 1px solid #313244;
    border-radius: 8px; padding: 32px; }}
  h1 {{ font-family: "Space Grotesk", system-ui, sans-serif; font-size: 20px;
    font-weight: 600; margin: 0 0 16px; letter-spacing: -0.01em; }}
  p {{ color: #a6adc8; font-size: 14px; line-height: 1.5; margin: 0 0 12px; }}
  .host {{ color: #cdd6f4; }}
  .row {{ display: flex; gap: 12px; margin-top: 24px; }}
  button {{ flex: 1; padding: 12px; border-radius: 8px; border: 1px solid #313244;
    font: inherit; font-weight: 600; cursor: pointer; }}
  button.approve {{ background: #fab387; color: #11111b; border-color: #fab387; }}
  button.deny {{ background: transparent; color: #cdd6f4; }}
</style>
</head>
<body>
<main>
  <h1>let {name} use your shopping list?</h1>
  <p>signed in as <span class="host">{who}</span>. {name} will be able to read
  and change the household list on your behalf.</p>
  <p>you'll be returned to <span class="host">{host}</span>.</p>
  <form method="post" action="{html.escape(action)}">
    {fields}
    <div class="row">
      <button class="deny" type="submit" name="decision" value="deny">not now</button>
      <button class="approve" type="submit" name="decision" value="approve">allow</button>
    </div>
  </form>
</main>
</body>
</html>"""


# --- access-token verification (for /mcp) ----------------------------------


def verify_access_token(request, token: str) -> dict | None:
    """Validate a presented OAuth access token against ``/mcp``: known, active
    (unrevoked, unexpired) and RFC 8707 resource-bound to this MCP endpoint.
    Returns the token row (carrying the granting ``user_id``) or None.
    Resource binding: a token minted for a different resource is rejected here
    even if otherwise valid — a token stolen from another audience can't be
    replayed at ``/mcp``."""
    oauth_repo = getattr(request.app.state, "oauth_repo", None)
    if oauth_repo is None:
        return None
    row = oauth_repo.get_active_access_token(token_hash(token))
    if row is None:
        return None
    settings = request.app.state.settings
    expected = mcp_resource(settings.origin)
    if row.get("resource") and row["resource"] != expected:
        return None
    return row
