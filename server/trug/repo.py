import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import uuid6

from trug.icons import lookup as icon_lookup
from trug.normalise import normalise, tidy_name

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    note TEXT,
    icon TEXT,
    category TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    source TEXT,
    added_by TEXT,
    created_at TEXT NOT NULL,
    checked_at TEXT,
    sort_key REAL
);

CREATE TABLE IF NOT EXISTS catalog (
    name_norm TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    icon TEXT,
    category TEXT,
    times_added INTEGER NOT NULL DEFAULT 0,
    last_added TEXT
);
"""

_ITEM_COLUMNS = (
    "id", "name", "note", "icon", "category",
    "status", "source", "added_by", "created_at", "checked_at", "sort_key",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _epoch(iso: str) -> float:
    """Epoch seconds for an ISO-8601 timestamp — the sort_key backfill/default."""
    return datetime.fromisoformat(iso).timestamp()


def _merge_notes(existing: str | None, incoming: str | None) -> str | None:
    """Absorb ``incoming`` into ``existing`` as a "; "-joined note, skipping an
    incoming segment that is already present so re-adds don't stutter the note.
    """
    if not incoming:
        return existing
    if not existing:
        return incoming
    segments = [s.strip() for s in existing.split(";")]
    if incoming.strip() in segments:
        return existing
    return f"{existing}; {incoming}"


def _frecency(times_added: int, last_added: str, now: datetime) -> float:
    days = max((now - datetime.fromisoformat(last_added)).total_seconds() / 86400, 0)
    return times_added * 0.5 ** (days / 30)


class Repository:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = self._connect()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """In-place schema upgrades for DBs created before a column existed.

        Adds items.sort_key when missing and backfills any NULL sort_keys from
        each row's created_at (epoch seconds), so pre-existing lists keep their
        creation order under the new `ORDER BY sort_key` list query.
        """
        with self._lock:
            cols = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(items)")
            }
            if "sort_key" not in cols:
                self._conn.execute("ALTER TABLE items ADD COLUMN sort_key REAL")
            rows = self._conn.execute(
                "SELECT id, created_at FROM items WHERE sort_key IS NULL"
            ).fetchall()
            for row in rows:
                self._conn.execute(
                    "UPDATE items SET sort_key = ? WHERE id = ?",
                    (_epoch(row["created_at"]), row["id"]),
                )
            self._conn.commit()
        self._tidy_existing_names()
        self._collapse_duplicates()
        self._migrate_nut_icon()

    def _migrate_nut_icon(self) -> None:
        """One-time backfill: rename the retired ``nut`` icon slug (a Tabler
        hex-nut/hardware glyph) to ``acorn`` (edible nuts) on already-enriched
        rows. Idempotent: a second run finds no ``icon = 'nut'`` rows left."""
        with self._lock:
            self._conn.execute(
                "UPDATE catalog SET icon = 'acorn' WHERE icon = 'nut'"
            )
            self._conn.execute(
                "UPDATE items SET icon = 'acorn' WHERE icon = 'nut'"
            )
            self._conn.commit()

    def _collapse_duplicates(self) -> None:
        """One-time backfill: collapse legacy same-name_norm duplicate rows into
        one. Before the reactivate-on-re-add rule, re-adding a checked item
        inserted a fresh row, so a norm could accumulate several rows (the
        "two Coffee rows in the basket" report). Keep the best row per norm — any
        active row wins, else the most recently created — merge every other row's
        distinct note into the keeper, and delete the rest. Idempotent: a norm
        with a single row is skipped, so a second run finds nothing to collapse.
        """
        with self._lock:
            norms = [
                row["name_norm"]
                for row in self._conn.execute(
                    "SELECT name_norm FROM items "
                    "GROUP BY name_norm HAVING COUNT(*) > 1"
                ).fetchall()
            ]
            for norm in norms:
                rows = self._conn.execute(
                    "SELECT * FROM items WHERE name_norm = ? "
                    "ORDER BY (status = 'active') DESC, created_at DESC, id DESC",
                    (norm,),
                ).fetchall()
                keeper = rows[0]
                note = keeper["note"]
                for extra in rows[1:]:
                    note = _merge_notes(note, extra["note"])
                    self._conn.execute(
                        "DELETE FROM items WHERE id = ?", (extra["id"],)
                    )
                if note != keeper["note"]:
                    self._conn.execute(
                        "UPDATE items SET note = ? WHERE id = ?", (note, keeper["id"])
                    )
            self._conn.commit()

    def _tidy_existing_names(self) -> None:
        """One-time backfill: apply tidy_name to pre-existing item names and
        catalog display names (name_norm untouched). Idempotent — tidy_name is a
        fixed point, so only rows that actually differ are rewritten, and a second
        run finds nothing to do."""
        with self._lock:
            for row in self._conn.execute("SELECT id, name FROM items").fetchall():
                tidied = tidy_name(row["name"])
                if tidied != row["name"]:
                    self._conn.execute(
                        "UPDATE items SET name = ? WHERE id = ?", (tidied, row["id"])
                    )
            for row in self._conn.execute(
                "SELECT name_norm, display_name FROM catalog"
            ).fetchall():
                tidied = tidy_name(row["display_name"])
                if tidied != row["display_name"]:
                    self._conn.execute(
                        "UPDATE catalog SET display_name = ? WHERE name_norm = ?",
                        (tidied, row["name_norm"]),
                    )
            self._conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Wait up to 5s for a writer lock instead of raising SQLITE_BUSY
        # immediately when this connection and trug/auth_repo.py's contend.
        conn.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _row_to_item(self, row: sqlite3.Row) -> dict:
        return {col: row[col] for col in _ITEM_COLUMNS}

    # --- items ---------------------------------------------------------

    def add_item(
        self,
        id: str | None,
        name: str,
        note: str | None,
        source: str,
        added_by: str | None,
    ) -> tuple[dict, bool]:
        name_norm = normalise(name, self.known_norms())
        # Cosmetic tidy for the stored/display name; dedup still keys off
        # name_norm (unchanged), so "bbq" and "Bbq" remain the same item.
        display = tidy_name(name)
        with self._lock:
            # Idempotency: existing id returns unchanged.
            if id is not None:
                existing = self._conn.execute(
                    "SELECT * FROM items WHERE id = ?", (id,)
                ).fetchone()
                if existing is not None:
                    return self._row_to_item(existing), False

            # Dedup: an active item sharing name_norm absorbs any new note.
            active = self._conn.execute(
                "SELECT * FROM items WHERE name_norm = ? AND status = 'active'",
                (name_norm,),
            ).fetchone()
            if active is not None:
                if note:
                    self._conn.execute(
                        "UPDATE items SET note = ? WHERE id = ?",
                        (_merge_notes(active["note"], note), active["id"]),
                    )
                # Dedup returns an existing item; only a genuine insert
                # bumps the catalog.
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT * FROM items WHERE id = ?", (active["id"],)
                ).fetchone()
                return self._row_to_item(row), False

            # Re-add of a checked same-norm item REACTIVATES it in place rather
            # than inserting a fresh duplicate into the basket: status returns to
            # active, checked_at clears, any distinct note merges, and — because a
            # re-add of something already bought is a genuine "buy it again"
            # intent — the catalog bumps like a real add. Returns created=False so
            # callers publish item_updated (the SSE upsert-by-id then moves the row
            # from basket to shelf). If legacy data holds several checked dupes,
            # reactivate the most recently created and leave the rest for the
            # _collapse_duplicates migration.
            checked = self._conn.execute(
                "SELECT * FROM items WHERE name_norm = ? AND status = 'checked' "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (name_norm,),
            ).fetchone()
            if checked is not None:
                self._bump_catalog(name_norm, display)
                self._conn.execute(
                    "UPDATE items SET status = 'active', checked_at = NULL, note = ?, "
                    "sort_key = ? WHERE id = ?",
                    (_merge_notes(checked["note"], note), _epoch(_now()), checked["id"]),
                )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT * FROM items WHERE id = ?", (checked["id"],)
                ).fetchone()
                return self._row_to_item(row), False

            new_id = id if id is not None else str(uuid6.uuid7())
            # Ensure the catalog row exists before enrichment tiers read it.
            self._bump_catalog(name_norm, display)
            # Tier 0: apply the built-in icon map once, only when no icon has
            # been recorded yet (mirrors set_enrichment's WHERE icon IS NULL
            # guard so an LLM-supplied icon is never overwritten).
            builtin = icon_lookup(name_norm)
            if builtin is not None:
                self._conn.execute(
                    "UPDATE catalog SET icon = ?, category = ? "
                    "WHERE name_norm = ? AND icon IS NULL",
                    (builtin[0], builtin[1], name_norm),
                )
            cat = self._conn.execute(
                "SELECT icon, category FROM catalog WHERE name_norm = ?",
                (name_norm,),
            ).fetchone()
            icon = cat["icon"] if cat is not None else None
            category = cat["category"] if cat is not None else None
            now = _now()
            self._conn.execute(
                "INSERT INTO items "
                "(id, name, name_norm, note, icon, category, status, "
                "source, added_by, created_at, checked_at, sort_key) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, NULL, ?)",
                (new_id, display, name_norm, note, icon, category,
                 source, added_by, now, _epoch(now)),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM items WHERE id = ?", (new_id,)
            ).fetchone()
            return self._row_to_item(row), True

    def _bump_catalog(self, name_norm: str, display_name: str) -> None:
        now = _now()
        self._conn.execute(
            "INSERT INTO catalog "
            "(name_norm, display_name, times_added, last_added) "
            "VALUES (?, ?, 1, ?) "
            "ON CONFLICT(name_norm) DO UPDATE SET "
            "times_added = times_added + 1, last_added = excluded.last_added",
            (name_norm, display_name, now),
        )

    def get_item(self, item_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        return self._row_to_item(row) if row is not None else None

    def set_status(self, item_id: str, status: str) -> dict | None:
        return self.update_item(item_id, status=status)

    def update_item(self, item_id: str, **fields) -> dict | None:
        allowed = {"name", "note", "icon", "category", "status", "sort_key"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_item(item_id)
        # Maintain checked_at alongside any status change.
        if "status" in updates:
            updates["checked_at"] = _now() if updates["status"] == "checked" else None
        assignments = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [item_id]
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE items SET {assignments} WHERE id = ?", values
            )
            self._conn.commit()
            if cur.rowcount == 0:
                return None
            # Re-read inside the lock so a concurrent delete can't turn a
            # successful mutation into None.
            row = self._conn.execute(
                "SELECT * FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        return self._row_to_item(row) if row is not None else None

    def delete_item(self, item_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM items WHERE id = ?", (item_id,)
            )
            self._conn.commit()
            return cur.rowcount > 0

    def clear_checked(self) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM items WHERE status = 'checked'"
            )
            self._conn.commit()
            return cur.rowcount

    def list_items(self) -> dict:
        rows = self._conn.execute(
            "SELECT * FROM items ORDER BY sort_key, created_at, id"
        ).fetchall()
        result: dict = {"active": [], "checked": []}
        for row in rows:
            item = self._row_to_item(row)
            bucket = "checked" if item["status"] == "checked" else "active"
            result[bucket].append(item)
        return result

    # --- catalog -------------------------------------------------------

    def known_norms(self) -> set[str]:
        rows = self._conn.execute("SELECT name_norm FROM catalog").fetchall()
        return {row["name_norm"] for row in rows}

    def catalog_entry(self, name_norm: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM catalog WHERE name_norm = ?", (name_norm,)
        ).fetchone()
        return dict(row) if row is not None else None

    def _rank(self, rows) -> list[dict]:
        now = datetime.now(timezone.utc)
        ranked = sorted(
            (dict(row) for row in rows),
            key=lambda r: _frecency(r["times_added"], r["last_added"], now),
            reverse=True,
        )
        return ranked

    def catalog_top(self, n: int = 24) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM catalog").fetchall()
        return self._rank(rows)[:n]

    def catalog_search(self, q: str, limit: int = 8) -> list[dict]:
        pattern = f"%{q}%"
        rows = self._conn.execute(
            "SELECT * FROM catalog "
            "WHERE name_norm LIKE ? OR display_name LIKE ?",
            (pattern, pattern),
        ).fetchall()
        now = datetime.now(timezone.utc)
        prefix = q.lower()

        def sort_key(r: dict):
            is_prefix = r["name_norm"].startswith(prefix) or \
                r["display_name"].lower().startswith(prefix)
            frec = _frecency(r["times_added"], r["last_added"], now)
            return (0 if is_prefix else 1, -frec)

        ranked = sorted((dict(row) for row in rows), key=sort_key)
        return ranked[:limit]

    def set_enrichment(
        self,
        name_norm: str,
        display_name: str,
        icon: str | None,
        category: str | None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE catalog SET display_name = ?, icon = ?, category = ? "
                "WHERE name_norm = ? AND icon IS NULL",
                (display_name, icon, category, name_norm),
            )
            self._conn.commit()
