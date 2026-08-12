"""Persistence for the in-app LLM (BYOK) configuration.

A single-row ``llm_config`` table holding the provider, api key, model and
base url a signed-in human picked in Settings. Lives in its own module and
owns its own table so ``trug/repo.py`` (items/catalog) and ``trug/auth_repo.py``
(passkeys) stay focused; it shares the same SQLite file via a separate
connection, and its table is disjoint from theirs. All SQL for the config lives
here — nothing leaks into the routes.

Key at rest
-----------
If ``TRUG_SECRET`` is set the api key is encrypted with Fernet, using a key
derived from the secret (SHA-256 → urlsafe-base64). Without a secret the key is
stored in plaintext in the SQLite file — acceptable for a single-household
self-host where the DB file is already the trust boundary, but set
``TRUG_SECRET`` if the DB might be backed up or shared. The decrypted key never
leaves the server: routes return only a masked hint, never the full key.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("trug.llm_config")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    provider TEXT NOT NULL,
    api_key TEXT,
    encrypted INTEGER NOT NULL DEFAULT 0,
    model TEXT,
    base_url TEXT,
    updated_at TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fernet_for(secret: str) -> Fernet:
    """Derive a Fernet cipher from ``secret`` (SHA-256 → urlsafe-base64 key)."""
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


class LLMConfigStore:
    """Single-row store for the BYOK LLM configuration."""

    def __init__(self, path: str | Path, secret: str | None = None) -> None:
        self.path = str(path)
        # A non-empty secret enables at-rest encryption of the api key.
        self._fernet = _fernet_for(secret) if secret else None
        # CONNECTION-ACCESS lock, not a write lock. THE RULE: every use of
        # ``self._conn`` — reads included — happens while holding this, and rows
        # are fully materialised (``.fetchone()``) before it is released. A
        # sqlite3 connection caches prepared statements keyed by SQL TEXT, so two
        # threads running the SAME query string share one underlying
        # ``sqlite3_stmt``: one rebinds and resets it while the other steps it,
        # yielding torn rows or InterfaceError. The connection is opened
        # ``check_same_thread=False``, so nothing else serialises this. Reentrant
        # so a locked method calling another locked one cannot deadlock; the cost
        # is that an inner ``commit()`` ends the outer transaction.
        self._lock = threading.RLock()
        self._conn = self._connect()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # --- encryption helpers -------------------------------------------------

    def _encrypt(self, plaintext: str) -> tuple[str, int]:
        """Return ``(stored_value, encrypted_flag)`` for an api key."""
        if self._fernet is None:
            return plaintext, 0
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii"), 1

    def _decrypt(self, stored: str | None, encrypted: int) -> str | None:
        if stored is None:
            return None
        if not encrypted:
            return stored
        if self._fernet is None:
            # Row was written with a secret that is now gone; the key is
            # unrecoverable. Treat as no key rather than crashing — but never
            # do so silently, and never log the ciphertext/key itself.
            logger.warning(
                "stored LLM key unreadable — TRUG_SECRET rotated or removed; "
                "re-enter the key in Settings to restore enrichment"
            )
            return None
        try:
            return self._fernet.decrypt(stored.encode("ascii")).decode("utf-8")
        except InvalidToken:
            logger.warning(
                "stored LLM key unreadable — corrupted ciphertext; "
                "re-enter the key in Settings to restore enrichment"
            )
            return None

    # --- public API ---------------------------------------------------------

    def get(self) -> dict | None:
        """The stored config with the api key decrypted, or ``None`` if unset.

        The returned ``api_key`` is the real key for server-side client
        building — callers must never hand it back to a client.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT provider, api_key, encrypted, model, base_url, updated_at "
                "FROM llm_config WHERE id = 1"
            ).fetchone()
        if row is None:
            return None
        api_key = self._decrypt(row["api_key"], row["encrypted"])
        # An encrypted row that failed to decrypt (rotated/removed secret or
        # corrupt ciphertext) has a stored value but no usable key. Flag it so
        # callers can fall back to env and the UI can prompt a re-entry.
        unreadable_key = (
            bool(row["encrypted"]) and row["api_key"] is not None and api_key is None
        )
        return {
            "provider": row["provider"],
            "api_key": api_key,
            "model": row["model"],
            "base_url": row["base_url"],
            "updated_at": row["updated_at"],
            "unreadable_key": unreadable_key,
        }

    def save(
        self,
        provider: str,
        api_key: str | None,
        model: str | None,
        base_url: str | None,
    ) -> None:
        """Upsert the single config row. ``api_key`` is encrypted if a secret
        is configured; otherwise stored in plaintext."""
        stored_key: str | None
        encrypted: int
        if api_key:
            stored_key, encrypted = self._encrypt(api_key)
        else:
            stored_key, encrypted = None, 0
        with self._lock:
            self._conn.execute(
                "INSERT INTO llm_config "
                "(id, provider, api_key, encrypted, model, base_url, updated_at) "
                "VALUES (1, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "provider=excluded.provider, api_key=excluded.api_key, "
                "encrypted=excluded.encrypted, model=excluded.model, "
                "base_url=excluded.base_url, updated_at=excluded.updated_at",
                (provider, stored_key, encrypted, model, base_url, _now_iso()),
            )
            self._conn.commit()

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM llm_config WHERE id = 1")
            self._conn.commit()

    @property
    def encrypts(self) -> bool:
        """Whether api keys are encrypted at rest (a secret is configured)."""
        return self._fernet is not None
