"""Concurrency regression tests for the repository layer.

Each repository holds ONE ``sqlite3.Connection`` opened with
``check_same_thread=False``. A sqlite3 connection keeps a per-connection
prepared-statement cache keyed by SQL TEXT, so two threads running the
*identical* SQL string concurrently share one underlying ``sqlite3_stmt``: one
thread rebinds/resets it while the other is still stepping it. The observed
consequences were not merely errors — a lookup could return a complete, coherent
row belonging to the OTHER user, i.e. one member's session authenticating as
another.

The fix is that ``self._lock`` is a CONNECTION-ACCESS lock, not a write lock:
every use of ``self._conn`` holds it. These tests defend that in two layers:

* ``test_*_takes_the_lock_for_every_connection_use`` — deterministic. Wraps the
  connection and asserts the lock is held for every ``execute``/``commit``. It
  cannot flake, and it fails immediately if anyone drops a ``with self._lock``.
* the threaded stress tests — real. They run the identical SQL on two threads
  with a tiny switch interval and assert no wrong-user / missing / garbled row.
  These reproduce the actual production symptom rather than a proxy for it.
"""

from __future__ import annotations

import sqlite3
import sys
import threading

import pytest

from trug.auth_repo import AuthRepository
from trug.llm_config import LLMConfigStore
from trug.oauth_repo import OAuthRepository
from trug.repo import Repository

# Enough iterations to make the unlocked race essentially certain while keeping
# the test to a fraction of a second once the lock is in place.
_ITERATIONS = 3000


def _lock_held(lock) -> bool:
    """True when the CURRENT thread holds ``lock``.

    ``threading.RLock`` exposes ``_is_owned()``; a plain ``threading.Lock`` does
    not, so fall back to ``locked()`` — accurate here because the lock-audit
    tests are single-threaded.
    """
    is_owned = getattr(lock, "_is_owned", None)
    if is_owned is not None:
        return is_owned()
    return lock.locked()


class _LockAuditingConnection:
    """Wraps a live connection and records every use made without the lock."""

    _GUARDED = ("execute", "executemany", "executescript", "commit", "rollback", "cursor")

    def __init__(self, conn: sqlite3.Connection, lock):
        self._conn = conn
        self._lock = lock
        self.violations: list[str] = []

    def __getattr__(self, name):
        attr = getattr(self._conn, name)
        if name not in self._GUARDED:
            return attr

        def guarded(*args, **kwargs):
            if not _lock_held(self._lock):
                sql = args[0] if args and isinstance(args[0], str) else ""
                self.violations.append(f"{name}: {' '.join(sql.split())[:90]}")
            return attr(*args, **kwargs)

        return guarded


def _audit(repo):
    """Swap in the auditing wrapper; returns it so callers can read violations.

    Installed AFTER construction: ``__init__`` runs single-threaded before the
    repo is reachable by any other thread, so its unlocked use is not a bug.
    """
    audit = _LockAuditingConnection(repo._conn, repo._lock)
    repo._conn = audit
    return audit


def test_auth_repo_takes_the_lock_for_every_connection_use(tmp_path):
    repo = AuthRepository(tmp_path / "auth.db")
    audit = _audit(repo)

    repo.seed_users(["alice", "bob"])
    alice = repo.get_user_by_name("alice")
    repo.journal_mode()
    repo.user_count()
    repo.enrolled_count()
    repo.list_users()
    repo.get_user(alice["id"])
    repo.create_user("carol")
    repo.add_credential(alice["id"], "cred-a", b"key", 0, "internal")
    repo.get_credential("cred-a")
    repo.credentials_for_user(alice["id"])
    repo.update_sign_count("cred-a", 1)
    repo.store_challenge("chal", "register", 300)
    repo.consume_challenge("chal", "register")
    repo.create_invite("inv-hash", alice["id"], 3600)
    repo.get_valid_invite("inv-hash")
    repo.redeem_invite_with_credential("inv-hash", "cred-r", b"key", 0, None)
    repo.redeem_invite_with_credential("inv-hash", "cred-r2", b"key", 0, None)  # loser path
    session_id = repo.create_session("sess-hash", alice["id"], 30, "agent")
    repo.get_active_session("sess-hash")
    repo.touch_session(session_id, 30)
    repo.sessions_for_user(alice["id"])
    repo.active_session_count()
    repo.outstanding_invites()
    repo.revoke_session(session_id, alice["id"])
    repo.revoke_all_sessions()
    repo.reopen_bootstrap()
    repo.bootstrap_reopen_active()
    repo.clear_bootstrap_reopen()
    repo.mark_forwarded_header_seen()
    repo.forwarded_header_seen()
    repo.record_host("trug.example")
    repo.observed_hosts()
    repo.delete_member_unless_last_enrolled("carol")
    repo.delete_user("carol")
    repo.claim_first_user(
        "dave", "cred-d", b"key", 0, None, "sess-d", 30, None, reopen=True
    )

    assert audit.violations == []


def test_repo_takes_the_lock_for_every_connection_use(tmp_path):
    repo = Repository(tmp_path / "items.db")
    audit = _audit(repo)

    item, _ = repo.add_item(None, "Lemons", "two", "web", "alice")
    repo.get_item(item["id"])
    repo.list_items()
    repo.update_item(item["id"], note="three")
    repo.update_item(item["id"])  # no-op update path routes through get_item
    repo.set_status(item["id"], "checked")
    repo.known_norms()
    repo.catalog_entry("lemons")
    repo.catalog_top()
    repo.catalog_search("lem")
    repo.set_enrichment("lemons", "Lemons", "lemon", "Fruit & Veg")
    repo.forget_catalog("nothing under this key")  # miss
    repo.forget_catalog("lemons")  # hit
    repo.clear_checked()
    repo.delete_item(item["id"])

    assert audit.violations == []


def test_oauth_repo_takes_the_lock_for_every_connection_use(tmp_path):
    repo = OAuthRepository(tmp_path / "oauth.db")
    audit = _audit(repo)

    repo.register_client("client-1", "Client", ["https://example.test/cb"], "none")
    repo.get_client("client-1")
    repo.cache_cimd("client-1", "Client", ["https://example.test/cb"], 300)
    repo.get_cached_cimd("client-1")
    repo.create_code(
        "code-hash", "client-1", "user-1", "https://example.test/cb",
        "challenge", "S256", None, None, 300,
    )
    repo.consume_code("code-hash")
    repo.create_access_token("at-hash", "client-1", "user-1", None, None, 300)
    repo.get_active_access_token("at-hash")
    repo.create_refresh_token("rt-hash", "client-1", "user-1", None, None, 300)
    repo.rotate_refresh_token("rt-hash")
    repo.revoke_refresh_token("rt-hash")
    repo.grants_for_user("user-1")

    assert audit.violations == []


def test_llm_config_store_takes_the_lock_for_every_connection_use(tmp_path):
    """The BYOK key store is the fourth module holding a shared connection, and
    ``get()`` runs on every enrichment — so it races the same way."""
    store = LLMConfigStore(tmp_path / "llm.db", secret="s3cret")
    audit = _audit(store)

    store.save("anthropic", "sk-test", "model", None)
    store.get()
    store.clear()

    assert audit.violations == []


@pytest.fixture
def hair_trigger_switching():
    """Force the interpreter to switch threads roughly every bytecode, so the
    identical-SQL window between bind and step is hit reliably rather than once
    in a few hundred iterations."""
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)


def _race(worker, *, threads=2):
    """Run ``worker(index)`` on N threads, re-raising the first thread failure
    in the main thread (never a downstream KeyError from a missing result)."""
    failures: list[BaseException] = []
    results: dict[int, object] = {}

    def run(i):
        try:
            results[i] = worker(i)
        except BaseException as exc:  # noqa: BLE001 — re-raised below
            failures.append(exc)

    workers = [threading.Thread(target=run, args=(i,)) for i in range(threads)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    if failures:
        raise failures[0]
    return results


def test_concurrent_get_active_session_never_returns_another_users_row(
    tmp_path, hair_trigger_switching
):
    """Two threads looking up two different sessions through the SAME SQL. Every
    lookup must return its own user's row — the unlocked version returned the
    other member's complete row, expired/None for live sessions, or raised
    InterfaceError/TypeError from a half-rebound statement."""
    repo = AuthRepository(tmp_path / "auth.db")
    repo.seed_users(["alice", "bob"])
    tokens = {}
    for name in ("alice", "bob"):
        uid = repo.get_user_by_name(name)["id"]
        tokens[name] = f"session-{name}"
        repo.create_session(tokens[name], uid, 30, name)

    names = ("alice", "bob")

    def worker(i):
        name = names[i]
        token = tokens[name]
        for _ in range(_ITERATIONS):
            row = repo.get_active_session(token)
            assert row is not None, f"live session for {name} reported invalid"
            assert row["user_name"] == name, (
                f"{name}'s session token resolved to user {row['user_name']!r}"
            )
            assert row["token_hash"] == token

    _race(worker)


def test_concurrent_get_valid_invite_never_returns_another_users_invite(
    tmp_path, hair_trigger_switching
):
    """The invite path showed the same wrong-row symptom, which would enrol a
    passkey against the wrong household member."""
    repo = AuthRepository(tmp_path / "auth.db")
    repo.seed_users(["alice", "bob"])
    user_ids = {}
    for name in ("alice", "bob"):
        user_ids[name] = repo.get_user_by_name(name)["id"]
        repo.create_invite(f"invite-{name}", user_ids[name], 3600)

    names = ("alice", "bob")

    def worker(i):
        name = names[i]
        token = f"invite-{name}"
        for _ in range(_ITERATIONS):
            invite = repo.get_valid_invite(token)
            assert invite is not None, f"valid invite for {name} reported invalid"
            assert invite["user_id"] == user_ids[name], (
                f"{name}'s invite resolved to another member's user_id"
            )

    _race(worker)


def test_concurrent_reads_during_writes_stay_coherent(tmp_path, hair_trigger_switching):
    """A reader and a writer on the shopping-list repo: the reader's rows must
    always be well-formed items, never a torn read of the writer's statement."""
    repo = Repository(tmp_path / "items.db")
    item, _ = repo.add_item(None, "Lemons", None, "web", "alice")

    def worker(i):
        if i == 0:
            for n in range(_ITERATIONS // 10):
                repo.update_item(item["id"], note=f"note-{n}")
        else:
            for _ in range(_ITERATIONS):
                got = repo.get_item(item["id"])
                assert got is not None, "existing item read as missing"
                assert got["id"] == item["id"]
                assert got["name"] == "Lemons"

    _race(worker)
