"""Persistence for the in-process OAuth 2.1 authorization server.

Lives in its own module and owns its own tables, mirroring ``auth_repo.py``:
the shopping-list repository stays focused on items, the passkey repository on
users/sessions, and this one on the OAuth surface (registered clients, cached
CIMD documents, authorization codes, access + refresh tokens). It shares the
same SQLite file via a separate connection; the tables are disjoint so the
connections never contend for the same rows. All OAuth SQL lives here — nothing
leaks into the routes.

Every credential is stored hashed (sha256 of the opaque value); a database leak
never yields a usable code or token. Lookups are by the full hash value.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uuid6

_SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY,
    client_name TEXT,
    redirect_uris TEXT NOT NULL,
    token_endpoint_auth_method TEXT NOT NULL DEFAULT 'none',
    registration_method TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_cimd_cache (
    client_id TEXT PRIMARY KEY,
    client_name TEXT,
    redirect_uris TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_codes (
    code_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    code_challenge_method TEXT NOT NULL,
    resource TEXT,
    scope TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS oauth_access_tokens (
    token_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    resource TEXT,
    scope TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    resource TEXT,
    scope TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    rotated_at TEXT,
    revoked INTEGER NOT NULL DEFAULT 0
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


class OAuthRepository:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = self._connect()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Wait up to 5s for a writer lock instead of raising SQLITE_BUSY
        # immediately when this connection contends with the others.
        conn.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # --- clients (DCR) -------------------------------------------------

    def register_client(
        self,
        client_id: str,
        client_name: str | None,
        redirect_uris: list[str],
        token_endpoint_auth_method: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO oauth_clients "
                "(client_id, client_name, redirect_uris, "
                "token_endpoint_auth_method, registration_method, created_at) "
                "VALUES (?, ?, ?, ?, 'dcr', ?)",
                (
                    client_id,
                    client_name,
                    json.dumps(redirect_uris),
                    token_endpoint_auth_method,
                    _iso(_now()),
                ),
            )
            self._conn.commit()

    def get_client(self, client_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM oauth_clients WHERE client_id = ?", (client_id,)
        ).fetchone()
        if row is None:
            return None
        client = dict(row)
        client["redirect_uris"] = json.loads(client["redirect_uris"])
        return client

    # --- CIMD cache ----------------------------------------------------

    def cache_cimd(
        self,
        client_id: str,
        client_name: str | None,
        redirect_uris: list[str],
        ttl_seconds: int,
    ) -> None:
        expires = _now() + timedelta(seconds=ttl_seconds)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO oauth_cimd_cache "
                "(client_id, client_name, redirect_uris, fetched_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    client_id,
                    client_name,
                    json.dumps(redirect_uris),
                    _iso(_now()),
                    _iso(expires),
                ),
            )
            self._conn.commit()

    def get_cached_cimd(self, client_id: str) -> dict | None:
        """Return the cached CIMD document iff still fresh; else None so the
        caller re-fetches."""
        row = self._conn.execute(
            "SELECT * FROM oauth_cimd_cache WHERE client_id = ?", (client_id,)
        ).fetchone()
        if row is None:
            return None
        if _parse(row["expires_at"]) < _now():
            return None
        client = dict(row)
        client["redirect_uris"] = json.loads(client["redirect_uris"])
        return client

    # --- authorization codes ------------------------------------------

    def create_code(
        self,
        code_hash: str,
        client_id: str,
        user_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str,
        resource: str | None,
        scope: str | None,
        ttl_seconds: int,
    ) -> None:
        expires = _now() + timedelta(seconds=ttl_seconds)
        with self._lock:
            self._conn.execute(
                "INSERT INTO oauth_codes "
                "(code_hash, client_id, user_id, redirect_uri, code_challenge, "
                "code_challenge_method, resource, scope, created_at, expires_at, "
                "used_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (
                    code_hash,
                    client_id,
                    user_id,
                    redirect_uri,
                    code_challenge,
                    code_challenge_method,
                    resource,
                    scope,
                    _iso(_now()),
                    _iso(expires),
                ),
            )
            self._conn.commit()

    def consume_code(self, code_hash: str) -> dict | None:
        """Single-use + expiry check. Returns the code row (as a dict) only for
        a fresh, unexpired, previously-unused code, marking it used atomically
        via ``UPDATE ... WHERE used_at IS NULL`` so a replayed code can never be
        redeemed twice. Returns None for missing/expired/already-used codes."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM oauth_codes WHERE code_hash = ?", (code_hash,)
            ).fetchone()
            if row is None or row["used_at"] is not None:
                return None
            if _parse(row["expires_at"]) < _now():
                return None
            cur = self._conn.execute(
                "UPDATE oauth_codes SET used_at = ? "
                "WHERE code_hash = ? AND used_at IS NULL",
                (_iso(_now()), code_hash),
            )
            if cur.rowcount == 0:
                # Lost a race to another redemption of the same code.
                self._conn.commit()
                return None
            self._conn.commit()
            return dict(row)

    # --- access tokens -------------------------------------------------

    def create_access_token(
        self,
        token_hash: str,
        client_id: str,
        user_id: str,
        resource: str | None,
        scope: str | None,
        ttl_seconds: int,
    ) -> None:
        expires = _now() + timedelta(seconds=ttl_seconds)
        with self._lock:
            self._conn.execute(
                "INSERT INTO oauth_access_tokens "
                "(token_hash, client_id, user_id, resource, scope, created_at, "
                "expires_at, revoked) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    token_hash,
                    client_id,
                    user_id,
                    resource,
                    scope,
                    _iso(_now()),
                    _iso(expires),
                ),
            )
            self._conn.commit()

    def get_active_access_token(self, token_hash: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM oauth_access_tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if row is None or row["revoked"]:
            return None
        if _parse(row["expires_at"]) < _now():
            return None
        return dict(row)

    # --- refresh tokens ------------------------------------------------

    def create_refresh_token(
        self,
        token_hash: str,
        client_id: str,
        user_id: str,
        resource: str | None,
        scope: str | None,
        ttl_seconds: int,
    ) -> None:
        expires = _now() + timedelta(seconds=ttl_seconds)
        with self._lock:
            self._conn.execute(
                "INSERT INTO oauth_refresh_tokens "
                "(token_hash, client_id, user_id, resource, scope, created_at, "
                "expires_at, rotated_at, revoked) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 0)",
                (
                    token_hash,
                    client_id,
                    user_id,
                    resource,
                    scope,
                    _iso(_now()),
                    _iso(expires),
                ),
            )
            self._conn.commit()

    def rotate_refresh_token(self, token_hash: str) -> dict | None:
        """Atomically consume a refresh token for rotation. Returns the row
        (client/user/resource/scope) only for the single caller that flips a
        valid, unrevoked, unexpired token to rotated; returns None otherwise
        (unknown, already-rotated, revoked, or expired). The caller then mints a
        fresh refresh + access token in the same response."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM oauth_refresh_tokens WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None or row["revoked"] or row["rotated_at"] is not None:
                return None
            if _parse(row["expires_at"]) < _now():
                return None
            cur = self._conn.execute(
                "UPDATE oauth_refresh_tokens SET rotated_at = ? "
                "WHERE token_hash = ? AND rotated_at IS NULL AND revoked = 0",
                (_iso(_now()), token_hash),
            )
            if cur.rowcount == 0:
                self._conn.commit()
                return None
            self._conn.commit()
            return dict(row)

    def revoke_refresh_token(self, token_hash: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE oauth_refresh_tokens SET revoked = 1 WHERE token_hash = ?",
                (token_hash,),
            )
            self._conn.commit()

    # --- listing (for later admin surfaces) ---------------------------

    def grants_for_user(self, user_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT client_id, scope, resource, created_at, expires_at "
            "FROM oauth_access_tokens WHERE user_id = ? AND revoked = 0 "
            "ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def new_id() -> str:
        return str(uuid6.uuid7())
