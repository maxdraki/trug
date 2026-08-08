"""Persistence for the passkey (WebAuthn) auth layer.

Lives in its own module and owns its own tables so the shopping-list
repository (``trug/repo.py``) stays focused on items and catalog. It shares the
same SQLite file as the main repository via a separate connection; the tables
are disjoint, so the two connections never contend for the same rows. All SQL
for auth lives here — nothing leaks into the routes.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uuid6

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS credentials (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    credential_id TEXT NOT NULL UNIQUE,
    public_key BLOB NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    transports TEXT,
    created_at TEXT NOT NULL,
    last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    user_agent TEXT,
    revoked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS invites (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS challenges (
    challenge TEXT PRIMARY KEY,
    purpose TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    invite_token_hash TEXT
);

-- Single-row operational meta. `bootstrap_reopened_at` is the non-destructive
-- recovery flag `trug-doctor recover --reset-bootstrap` sets to re-open the
-- one-time first-user claim without deleting any rows; the claim clears it again
-- on success, so the normal path stays strictly one-time.
-- `forwarded_header_seen_at` records the first time the server saw a proxy
-- forwarded header (X-Forwarded-For/-Host), so `trug-doctor` (a separate
-- process) can tell the app is behind a proxy and warn if the rate limiter is
-- still keying on the shared proxy IP.
CREATE TABLE IF NOT EXISTS meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    bootstrap_reopened_at TEXT,
    forwarded_header_seen_at TEXT
);

-- Bounded record of the distinct Host / X-Forwarded-Host values the server has
-- observed, so `trug-doctor` (a separate process) can compare them to the
-- configured TRUG_ORIGIN and catch the silent origin-mismatch failure. Only the
-- host string, a hit count and a last-seen timestamp are kept — never any
-- request body, path or other content — and the table is capped at a handful of
-- rows. It is the one shared channel a diagnostic CLI has to what the running
-- server is actually being reached as.
CREATE TABLE IF NOT EXISTS observed_hosts (
    host TEXT PRIMARY KEY,
    hit_count INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT NOT NULL
);
"""

# Keep at most this many distinct observed hosts, evicting the least-recently
# seen — enough to surface a mismatch, never an unbounded log.
_MAX_OBSERVED_HOSTS = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


class AuthRepository:
    def __init__(self, path: str | Path, *, read_only: bool = False):
        self.path = str(path)
        # Read-only mode is the diagnostic (``trug-doctor``) path: it opens the
        # existing DB without creating the schema, seeding meta, migrating or
        # switching journal mode — so a diagnosis never creates or mutates the
        # file (e.g. leaving a root-owned DB the server then can't open).
        self.read_only = read_only
        self._lock = threading.Lock()
        self._conn = self._connect()
        if read_only:
            return
        self._conn.executescript(_SCHEMA)
        # Migrate BEFORE seeding the meta row: on a DB created by an older schema
        # the meta table may lack forwarded_header_seen_at, and CREATE TABLE IF
        # NOT EXISTS won't add it — the migration does, so the INSERT below can
        # safely name the column.
        self._migrate()
        # Ensure the single meta row exists so the reopen flag is a plain UPDATE.
        self._conn.execute(
            "INSERT OR IGNORE INTO meta "
            "(id, bootstrap_reopened_at, forwarded_header_seen_at) "
            "VALUES (1, NULL, NULL)"
        )
        self._conn.commit()

    def _migrate(self) -> None:
        """In-place schema upgrades for DBs created before a column existed:
        ``invite_token_hash`` on challenges (so consume_invite can invalidate the
        challenges minted against an invite it just consumed) and
        ``forwarded_header_seen_at`` on meta (the proxy-detection signal)."""
        with self._lock:
            cols = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(challenges)")
            }
            if "invite_token_hash" not in cols:
                self._conn.execute(
                    "ALTER TABLE challenges ADD COLUMN invite_token_hash TEXT"
                )
                self._conn.commit()
            meta_cols = {
                row["name"] for row in self._conn.execute("PRAGMA table_info(meta)")
            }
            if "forwarded_header_seen_at" not in meta_cols:
                self._conn.execute(
                    "ALTER TABLE meta ADD COLUMN forwarded_header_seen_at TEXT"
                )
                self._conn.commit()

    def _connect(self) -> sqlite3.Connection:
        if self.read_only and self.path != ":memory:":
            # Open strictly read-only so a diagnosis can never create or write
            # the file. WAL databases are readable through a mode=ro handle.
            conn = sqlite3.connect(
                f"file:{self.path}?mode=ro", uri=True, check_same_thread=False
            )
        else:
            conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Wait up to 5s for a writer lock instead of raising SQLITE_BUSY
        # immediately when this connection and trug/repo.py's contend.
        conn.execute("PRAGMA busy_timeout = 5000")
        # Setting the journal mode is a write; skip it on a read-only handle.
        if self.path != ":memory:" and not self.read_only:
            conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def journal_mode(self) -> str:
        """The current SQLite journal mode (``"wal"`` on a normally-opened repo).
        Kept in the repository layer so the doctor never touches sqlite3 itself."""
        return self._conn.execute("PRAGMA journal_mode").fetchone()[0]

    # --- users ---------------------------------------------------------

    def seed_users(self, names: list[str]) -> None:
        """Insert users once; existing rows are left untouched. No longer used by
        the app (the roster is dynamic) — kept as a test/fixture convenience."""
        with self._lock:
            for name in names:
                row = self._conn.execute(
                    "SELECT id FROM users WHERE name = ?", (name,)
                ).fetchone()
                if row is None:
                    self._conn.execute(
                        "INSERT INTO users (id, name, display_name, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (str(uuid6.uuid7()), name, name, _iso(_now())),
                    )
            self._conn.commit()

    def user_count(self) -> int:
        """Total users on the roster (pending + enrolled). Zero means the
        instance is unclaimed and the bootstrap flow is open."""
        return self._conn.execute(
            "SELECT COUNT(*) AS n FROM users"
        ).fetchone()["n"]

    def enrolled_count(self) -> int:
        """Users with at least one credential — the ones who can actually sign in."""
        return self._conn.execute(
            "SELECT COUNT(DISTINCT c.user_id) AS n FROM credentials c"
        ).fetchone()["n"]

    def list_users(self) -> list[dict]:
        """The roster for the Members UI: each user's name, whether they are
        enrolled (≥1 credential), when they were created, and the credential
        count. Ordered oldest-first so the household reads stably."""
        rows = self._conn.execute(
            "SELECT u.name AS name, u.created_at AS created_at, "
            "COUNT(c.id) AS credential_count "
            "FROM users u LEFT JOIN credentials c ON c.user_id = u.id "
            "GROUP BY u.id ORDER BY u.created_at ASC"
        ).fetchall()
        return [
            {
                "name": r["name"],
                "enrolled": r["credential_count"] > 0,
                "created_at": r["created_at"],
                "credential_count": r["credential_count"],
            }
            for r in rows
        ]

    def create_user(self, name: str) -> dict:
        """Create a pending user. Raises ValueError on a duplicate name so a
        caller can surface a clear "already exists" rather than a bare IntegrityError."""
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM users WHERE name = ?", (name,)
            ).fetchone()
            if existing is not None:
                raise ValueError(f"User {name!r} already exists")
            uid = str(uuid6.uuid7())
            self._conn.execute(
                "INSERT INTO users (id, name, display_name, created_at) "
                "VALUES (?, ?, ?, ?)",
                (uid, name, name, _iso(_now())),
            )
            self._conn.commit()
            return {"id": uid, "name": name, "display_name": name}

    def claim_first_user(
        self,
        name: str,
        credential_id: str,
        public_key: bytes,
        sign_count: int,
        transports: str | None,
        session_token_hash: str,
        session_days: int,
        user_agent: str | None,
        reopen: bool = False,
    ) -> dict | None:
        """Claim the very first account as ONE atomic unit. Under the write lock,
        in a single transaction: re-check the roster is empty, then insert the
        user, their credential AND their session row — one commit for all three.
        Returns the user dict, or None if any user already exists (a lost
        bootstrap race).

        ``reopen`` is the recovery path (``recover --reset-bootstrap``): when the
        ``bootstrap_reopened_at`` flag is live, the claim is permitted even though
        users already exist. It attaches the new credential + session to the
        existing same-named user when there is one (re-enrolling a locked-out
        member, preserving their id and data) or creates a fresh user otherwise,
        then clears the reopen flag in the SAME transaction — so recovery is
        non-destructive and the one-time property is restored the moment it
        succeeds. The flag is re-checked under the write lock, so a concurrent
        claim that already consumed the reopen loses cleanly (None).

        Atomicity is the whole point: the old two-step flow (create user, commit;
        then add credential in a separate txn) could leave a committed user with
        zero credentials if the credential insert failed — user_count()!=0 then
        wedges bootstrap shut forever with no way to sign in. Here nothing is
        committed until all three inserts succeed; any failure rolls the lot back,
        so the roster is either fully claimed or still empty and re-claimable —
        never a half-claimed brick."""
        with self._lock:
            n = self._conn.execute(
                "SELECT COUNT(*) AS n FROM users"
            ).fetchone()["n"]
            reopened = (
                self._conn.execute(
                    "SELECT bootstrap_reopened_at FROM meta WHERE id = 1"
                ).fetchone()["bootstrap_reopened_at"]
                is not None
            )
            # Pristine claim: refuse if anyone exists. Recovery claim: allowed
            # only while the reopen flag is still live (re-checked here under the
            # lock so a racing claim that already consumed it is refused).
            if not reopen and n != 0:
                return None
            if reopen and not reopened:
                return None
            now = _now()
            existing = (
                self._conn.execute(
                    "SELECT id FROM users WHERE name = ?", (name,)
                ).fetchone()
                if reopen
                else None
            )
            uid = existing["id"] if existing is not None else str(uuid6.uuid7())
            try:
                if existing is None:
                    self._conn.execute(
                        "INSERT INTO users (id, name, display_name, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (uid, name, name, _iso(now)),
                    )
                self._conn.execute(
                    "INSERT INTO credentials "
                    "(id, user_id, credential_id, public_key, sign_count, "
                    "transports, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid6.uuid7()),
                        uid,
                        credential_id,
                        public_key,
                        sign_count,
                        transports,
                        _iso(now),
                    ),
                )
                expires = now + timedelta(days=session_days)
                self._conn.execute(
                    "INSERT INTO sessions "
                    "(id, token_hash, user_id, created_at, expires_at, "
                    "last_seen, user_agent, revoked) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                    (
                        str(uuid6.uuid7()),
                        session_token_hash,
                        uid,
                        _iso(now),
                        _iso(expires),
                        _iso(now),
                        user_agent,
                    ),
                )
                if reopen:
                    # Restore the one-time property atomically with the claim.
                    self._conn.execute(
                        "UPDATE meta SET bootstrap_reopened_at = NULL WHERE id = 1"
                    )
                self._conn.commit()
            except Exception:
                # Roll the whole claim back so no partial (user-without-credential)
                # state can leak into a later commit and brick the instance.
                self._conn.rollback()
                raise
            return {"id": uid, "name": name, "display_name": name}

    def _cascade_delete_user(self, uid: str) -> None:
        """Delete a user and their credentials, sessions and invites. Assumes the
        caller already holds ``self._lock`` and will commit."""
        self._conn.execute("DELETE FROM credentials WHERE user_id = ?", (uid,))
        self._conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
        self._conn.execute("DELETE FROM invites WHERE user_id = ?", (uid,))
        self._conn.execute("DELETE FROM users WHERE id = ?", (uid,))

    def delete_user(self, name: str) -> bool:
        """Remove a user and cascade their credentials, sessions and invites.
        Returns False if no such user. All four deletes run under the write lock
        so a member is never left half-removed. No last-member guard — callers
        that must preserve the sign-in invariant use
        ``delete_member_unless_last_enrolled`` instead."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM users WHERE name = ?", (name,)
            ).fetchone()
            if row is None:
                return False
            self._cascade_delete_user(row["id"])
            self._conn.commit()
            return True

    def delete_member_unless_last_enrolled(self, name: str) -> str:
        """Remove a member, but refuse if doing so would empty the roster of
        enrolled (credential-holding) members. Returns ``"not_found"``,
        ``"last_enrolled"`` (refused) or ``"ok"`` (deleted).

        The last-member check and the delete happen together under the write
        lock, in one transaction, so the invariant "at least one enrolled member
        always remains" holds even under concurrent removals. The old flow read
        the roster without the lock and deleted separately: two concurrent
        removals of the two last enrolled members could both observe >1 enrolled
        and both delete, leaving zero enrolled — and if any pending user lingered,
        user_count()>0 kept bootstrap inert, locking the household out for good."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM users WHERE name = ?", (name,)
            ).fetchone()
            if row is None:
                return "not_found"
            uid = row["id"]
            target_enrolled = (
                self._conn.execute(
                    "SELECT COUNT(*) AS n FROM credentials WHERE user_id = ?",
                    (uid,),
                ).fetchone()["n"]
                > 0
            )
            enrolled = self._conn.execute(
                "SELECT COUNT(DISTINCT user_id) AS n FROM credentials"
            ).fetchone()["n"]
            if target_enrolled and enrolled <= 1:
                return "last_enrolled"
            self._cascade_delete_user(uid)
            self._conn.commit()
            return "ok"

    def get_user_by_name(self, name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM users WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row is not None else None

    def get_user(self, user_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    # --- credentials ---------------------------------------------------

    def add_credential(
        self,
        user_id: str,
        credential_id: str,
        public_key: bytes,
        sign_count: int,
        transports: str | None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO credentials "
                "(id, user_id, credential_id, public_key, sign_count, "
                "transports, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid6.uuid7()),
                    user_id,
                    credential_id,
                    public_key,
                    sign_count,
                    transports,
                    _iso(_now()),
                ),
            )
            self._conn.commit()

    def get_credential(self, credential_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM credentials WHERE credential_id = ?", (credential_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def credentials_for_user(self, user_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM credentials WHERE user_id = ?", (user_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def update_sign_count(self, credential_id: str, new_count: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE credentials SET sign_count = ?, last_used_at = ? "
                "WHERE credential_id = ?",
                (new_count, _iso(_now()), credential_id),
            )
            self._conn.commit()

    # --- challenges ----------------------------------------------------

    def store_challenge(
        self,
        challenge: str,
        purpose: str,
        ttl_seconds: int,
        invite_token_hash: str | None = None,
    ) -> None:
        """``invite_token_hash``, when given, ties this challenge to an
        invite so consume_invite can invalidate it as a side effect of
        redemption (closing the window where an outstanding registration
        challenge survives its invite being used up elsewhere)."""
        expires = _now() + timedelta(seconds=ttl_seconds)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO challenges "
                "(challenge, purpose, expires_at, used_at, invite_token_hash) "
                "VALUES (?, ?, ?, NULL, ?)",
                (challenge, purpose, _iso(expires), invite_token_hash),
            )
            self._conn.commit()

    def consume_challenge(self, challenge: str, purpose: str) -> bool:
        """Single-use + expiry check. Returns True only for a fresh, unexpired
        challenge issued for ``purpose``; marks it used atomically."""
        with self._lock:
            row = self._conn.execute(
                "SELECT expires_at, used_at FROM challenges "
                "WHERE challenge = ? AND purpose = ?",
                (challenge, purpose),
            ).fetchone()
            if row is None or row["used_at"] is not None:
                return False
            if _parse(row["expires_at"]) < _now():
                return False
            self._conn.execute(
                "UPDATE challenges SET used_at = ? WHERE challenge = ?",
                (_iso(_now()), challenge),
            )
            self._conn.commit()
            return True

    # --- invites -------------------------------------------------------

    def create_invite(
        self, token_hash: str, user_id: str, ttl_seconds: int
    ) -> None:
        expires = _now() + timedelta(seconds=ttl_seconds)
        with self._lock:
            self._conn.execute(
                "INSERT INTO invites "
                "(token_hash, user_id, created_at, expires_at, used_at) "
                "VALUES (?, ?, ?, ?, NULL)",
                (token_hash, user_id, _iso(_now()), _iso(expires)),
            )
            self._conn.commit()

    def get_valid_invite(self, token_hash: str) -> dict | None:
        """Return the invite iff it exists, is unused, and is unexpired."""
        row = self._conn.execute(
            "SELECT * FROM invites WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if row is None or row["used_at"] is not None:
            return None
        if _parse(row["expires_at"]) < _now():
            return None
        return dict(row)

    def consume_invite(self, token_hash: str) -> bool:
        """Atomically mark an invite used. Returns True only for the caller
        that actually flips it (a plain UPDATE ... WHERE used_at IS NULL,
        checked via rowcount) so two racing redemptions can't both succeed.
        Also invalidates any still-unused challenges minted against this
        invite, so a stale register/options challenge from a losing race
        can't be replayed after the fact."""
        with self._lock:
            now = _iso(_now())
            cur = self._conn.execute(
                "UPDATE invites SET used_at = ? "
                "WHERE token_hash = ? AND used_at IS NULL",
                (now, token_hash),
            )
            consumed = cur.rowcount > 0
            if consumed:
                self._conn.execute(
                    "UPDATE challenges SET used_at = ? "
                    "WHERE invite_token_hash = ? AND used_at IS NULL",
                    (now, token_hash),
                )
            self._conn.commit()
            return consumed

    # --- sessions ------------------------------------------------------

    def create_session(
        self,
        token_hash: str,
        user_id: str,
        session_days: int,
        user_agent: str | None,
    ) -> str:
        now = _now()
        expires = now + timedelta(days=session_days)
        session_id = str(uuid6.uuid7())
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions "
                "(id, token_hash, user_id, created_at, expires_at, "
                "last_seen, user_agent, revoked) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    session_id,
                    token_hash,
                    user_id,
                    _iso(now),
                    _iso(expires),
                    _iso(now),
                    user_agent,
                ),
            )
            self._conn.commit()
        return session_id

    def get_active_session(self, token_hash: str) -> dict | None:
        """Return an active (unrevoked, unexpired) session with its user name."""
        row = self._conn.execute(
            "SELECT s.*, u.name AS user_name FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
            (token_hash,),
        ).fetchone()
        if row is None or row["revoked"]:
            return None
        if _parse(row["expires_at"]) < _now():
            return None
        return dict(row)

    def touch_session(self, session_id: str, session_days: int) -> None:
        """Update last_seen; extend expiry once past the window's half-life
        (rolling expiry) so active devices stay signed in without every
        request rewriting the expiry."""
        now = _now()
        with self._lock:
            row = self._conn.execute(
                "SELECT expires_at FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return
            expires = _parse(row["expires_at"])
            half_life = timedelta(days=session_days) / 2
            if expires - now < half_life:
                expires = now + timedelta(days=session_days)
            self._conn.execute(
                "UPDATE sessions SET last_seen = ?, expires_at = ? WHERE id = ?",
                (_iso(now), _iso(expires), session_id),
            )
            self._conn.commit()

    def sessions_for_user(self, user_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, created_at, last_seen, user_agent, revoked "
            "FROM sessions WHERE user_id = ? AND revoked = 0 "
            "ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def revoke_session(self, session_id: str, user_id: str) -> bool:
        """Revoke a session owned by ``user_id``. Returns False if it does not
        belong to the caller (so one user cannot revoke another's session)."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE sessions SET revoked = 1 WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def revoke_all_sessions(self) -> int:
        """Invalidate every session on the instance (the stolen-device answer).
        Passkeys are untouched, so everyone simply signs in again. Returns the
        number of previously-active sessions revoked."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE sessions SET revoked = 1 WHERE revoked = 0"
            )
            self._conn.commit()
            return cur.rowcount

    def active_session_count(self) -> int:
        """Unrevoked, unexpired sessions — the 'active sessions' the roster line
        reports."""
        now = _iso(_now())
        return self._conn.execute(
            "SELECT COUNT(*) AS n FROM sessions "
            "WHERE revoked = 0 AND expires_at > ?",
            (now,),
        ).fetchone()["n"]

    def outstanding_invites(self) -> int:
        """Unused, unexpired invites still redeemable — reported so a lockout
        diagnosis knows a way back in may already be in someone's inbox."""
        now = _iso(_now())
        return self._conn.execute(
            "SELECT COUNT(*) AS n FROM invites "
            "WHERE used_at IS NULL AND expires_at > ?",
            (now,),
        ).fetchone()["n"]

    # --- bootstrap reopen (recovery) -----------------------------------

    def reopen_bootstrap(self) -> None:
        """Set the non-destructive reopen flag so the first-user claim is live
        again despite existing users. Cleared automatically by a successful
        claim (see ``claim_first_user``)."""
        with self._lock:
            self._conn.execute(
                "UPDATE meta SET bootstrap_reopened_at = ? WHERE id = 1",
                (_iso(_now()),),
            )
            self._conn.commit()

    def clear_bootstrap_reopen(self) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE meta SET bootstrap_reopened_at = NULL WHERE id = 1"
            )
            self._conn.commit()

    def bootstrap_reopen_active(self) -> bool:
        """True while a recover --reset-bootstrap reopen is live and unclaimed."""
        row = self._conn.execute(
            "SELECT bootstrap_reopened_at FROM meta WHERE id = 1"
        ).fetchone()
        return row is not None and row["bootstrap_reopened_at"] is not None

    # --- proxy-detection signal (rate-limiter trust diagnostics) -------

    def mark_forwarded_header_seen(self) -> None:
        """Record (once) that the server has observed a proxy forwarded header
        (X-Forwarded-For / X-Forwarded-Host), so ``trug-doctor`` — a separate
        process — can tell the app is behind a proxy and warn if the rate limiter
        is still keying on the shared proxy IP. Idempotent: only the first
        observation's timestamp is kept (the UPDATE is a no-op once set).

        Best-effort observability like ``record_host``: it swallows its own
        storage errors HERE, in the repo layer, so a storage hiccup can never
        break the real request that triggered it, and the middleware never has to
        name sqlite3."""
        now = _iso(_now())
        with self._lock:
            try:
                self._conn.execute(
                    "UPDATE meta SET forwarded_header_seen_at = ? "
                    "WHERE id = 1 AND forwarded_header_seen_at IS NULL",
                    (now,),
                )
                self._conn.commit()
            except sqlite3.Error:
                self._conn.rollback()

    def forwarded_header_seen(self) -> bool:
        """True once a proxy forwarded header has been observed. A plain SELECT,
        so it works on the read-only diagnostic (``trug-doctor``) connection.

        Guarded against a pre-migration DB: the read-only constructor returns
        before ``_migrate()``, so on an upgraded-code-but-unmigrated file the
        ``forwarded_header_seen_at`` column may not exist yet. A missing column
        raises ``sqlite3.OperationalError`` — swallow it HERE (best-effort, like
        record_host/mark_forwarded_header_seen) and report 'not seen' rather than
        crash the whole diagnosis, keeping sqlite3 out of the doctor layer."""
        try:
            row = self._conn.execute(
                "SELECT forwarded_header_seen_at FROM meta WHERE id = 1"
            ).fetchone()
        except sqlite3.OperationalError:
            return False
        return row is not None and row["forwarded_header_seen_at"] is not None

    # --- observed hosts (origin-mismatch diagnostics) ------------------

    def record_host(self, host: str) -> None:
        """Record one observation of ``host`` (a Host / X-Forwarded-Host value),
        bumping its hit count and last-seen and evicting all but the most
        recently seen ``_MAX_OBSERVED_HOSTS`` distinct hosts. Stores only the
        host string — never a path, body or any request content."""
        if not host:
            return
        now = _iso(_now())
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO observed_hosts (host, hit_count, last_seen) "
                    "VALUES (?, 1, ?) "
                    "ON CONFLICT(host) DO UPDATE SET "
                    "hit_count = hit_count + 1, last_seen = excluded.last_seen",
                    (host, now),
                )
                self._conn.execute(
                    "DELETE FROM observed_hosts WHERE host NOT IN ("
                    "SELECT host FROM observed_hosts ORDER BY last_seen DESC LIMIT ?)",
                    (_MAX_OBSERVED_HOSTS,),
                )
                self._conn.commit()
            except sqlite3.Error:
                # Recording an observed host is pure best-effort observability
                # for the doctor; a storage hiccup here must never break the
                # real request that triggered it. Swallowed HERE, in the repo
                # layer, so the middleware never has to name sqlite3.
                self._conn.rollback()

    def observed_hosts(self) -> list[dict]:
        """The observed hosts, most-recently-seen first: each ``{host,
        hit_count, last_seen}``."""
        rows = self._conn.execute(
            "SELECT host, hit_count, last_seen FROM observed_hosts "
            "ORDER BY last_seen DESC"
        ).fetchall()
        return [dict(row) for row in rows]
