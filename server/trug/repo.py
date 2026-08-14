import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uuid6

from trug.categories import DEFAULT_WALK_ORDER, resolve_category
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
    last_added TEXT,
    retired_at TEXT
);
"""

# How long a deleted name's catalogue row stays STASHED — invisible everywhere,
# but revivable by re-adding the same name.
#
# The undo toast is five seconds, and five seconds would cover the reported bug
# exactly. It is the wrong number all the same: the shopper who swipes Milk off,
# carries on adding, and only two minutes later thinks "hang on, we do need
# milk" has precisely the same expectation — they did not intend to throw away
# 54 shops of history, and the interface never told them they had. An hour is
# the shape of that intent: it covers a whole shopping trip or a whole
# add-things-to-the-list sitting, so a correction made in the same breath as the
# mistake always lands, while a name that comes back next Saturday is a genuine
# new decision and starts a genuinely new shortcut. Nothing user-facing counts
# the seconds down, so a boundary the shopper can feel matters more than a tight
# one; the only cost of being generous is that a deliberate delete-then-re-add
# within the hour keeps history the shopper may have wanted reset — and the
# gesture for "this name is wrong" is forget_catalog, which is permanent.
STASH_WINDOW_SECONDS = 3600

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
    def __init__(self, path: str | Path, walk_order: list[str] | None = None):
        self.path = str(path)
        # The household's configured aisles. Every category the repository
        # writes — the built-in tier-0 map included — is resolved against this,
        # so a config that omits an aisle never has it invented for it.
        self.walk_order = list(walk_order) if walk_order else list(DEFAULT_WALK_ORDER)
        # CONNECTION-ACCESS lock, not a write lock. THE RULE: every use of
        # ``self._conn`` — reads included — happens while holding this, and rows
        # are fully materialised (``.fetchone()``/``.fetchall()``) before it is
        # released. A sqlite3 connection caches prepared statements keyed by SQL
        # TEXT, so two threads running the SAME query string share one underlying
        # ``sqlite3_stmt``: one rebinds and resets it while the other steps it,
        # yielding torn or outright wrong rows (and InterfaceError/TypeError).
        # The connection is opened ``check_same_thread=False``, so nothing else
        # serialises this. Reentrant so that a locked method calling another
        # locked one cannot deadlock the server; the cost is that an inner
        # ``commit()`` ends the outer transaction, so multi-statement atomic
        # units call non-committing private ``_`` helpers (see _bump_catalog).
        self._lock = threading.RLock()
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
        self._migrate_catalog_retired_at()
        self._tidy_existing_names()
        self._collapse_duplicates()
        self._migrate_nut_icon()
        self._migrate_coconut_milk_icon()
        self._migrate_builtin_map_refile()

    def _migrate_catalog_retired_at(self) -> None:
        """Add catalog.retired_at to DBs created before the stash existed.

        NULL is exactly the right value for every pre-existing row — none of
        them was deleted — so there is nothing to backfill. Must run before any
        query that mentions the column; ``CREATE TABLE IF NOT EXISTS`` leaves an
        existing table's columns alone, so a live household's DB reaches here
        without it. Idempotent: the second run sees the column and does nothing.
        """
        with self._lock:
            cols = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(catalog)")
            }
            if "retired_at" not in cols:
                self._conn.execute("ALTER TABLE catalog ADD COLUMN retired_at TEXT")
                self._conn.commit()

    def _builtin(self, name_norm: str) -> tuple[str, str] | None:
        """Tier 0 for ``name_norm``: ``(icon_slug, category)`` or ``None``.

        The map's category is resolved against the configured walk order, so a
        household that does not stock an aisle gets ``Other`` — the same
        fallback an unknown LLM-assigned category gets. Only the category is
        gated; the icon is aisle-independent and always applies.
        """
        builtin = icon_lookup(name_norm)
        if builtin is None:
            return None
        return builtin[0], resolve_category(builtin[1], self.walk_order)

    def _migrate_builtin_map_refile(self) -> None:
        """One-time backfill: re-file rows the built-in map can now place but
        couldn't when they were added (the "Herbs & Spices" aisle landed after
        Thyme, Cumin and friends were already sitting in ``Other``). Category and
        icon are stamped once, at insert, and nothing re-runs the lookup.

        Items are touched only when BOTH the stored category is the catch-all
        (``Other``/NULL) AND no icon has ever been recorded — i.e. the row is
        visibly unfiled, showing a monogram fallback. Any other category means a
        human or the LLM put it there deliberately, and silently re-filing
        someone's live list is not our call; an icon already present means
        enrichment has had its say. That icon guard is also what stops a
        boot-time re-run fighting the user: once re-filed the row carries an
        icon, so dragging it back to ``Other`` afterwards makes it permanently
        out of scope.

        The catalogue is the part that makes it stick. ``add_item`` applies the
        built-in map to a catalog row only ``WHERE icon IS NULL``, then copies
        the catalog's icon/category onto the new item — so a name already
        catalogued under the old aisle would keep landing there forever on every
        re-add. Rewriting is scoped to names the built-in map knows, where tier 0
        is authoritative and the stored value is only ever a stale copy of an
        older map (or an LLM guess made before the map learned the name). Names
        the map cannot place are left completely alone, so category knowledge the
        LLM learned for them survives; no user-facing path writes catalog
        icon/category, so nothing here can clobber a human choice.

        Idempotent: the second run recomputes the same map values, finds the
        items already carry an icon and the catalog rows already equal the map,
        and writes nothing.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name_norm FROM items "
                "WHERE icon IS NULL AND (category IS NULL OR category = 'Other')"
            ).fetchall()
            for row in rows:
                builtin = self._builtin(row["name_norm"])
                if builtin is None:
                    continue
                self._conn.execute(
                    "UPDATE items SET icon = ?, category = ? WHERE id = ?",
                    (builtin[0], builtin[1], row["id"]),
                )
            catalog = self._conn.execute(
                "SELECT name_norm, icon, category FROM catalog"
            ).fetchall()
            for row in catalog:
                builtin = self._builtin(row["name_norm"])
                if builtin is None:
                    continue
                if (row["icon"], row["category"]) == builtin:
                    continue
                self._conn.execute(
                    "UPDATE catalog SET icon = ?, category = ? WHERE name_norm = ?",
                    (builtin[0], builtin[1], row["name_norm"]),
                )
            self._conn.commit()

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

    def _migrate_coconut_milk_icon(self) -> None:
        """One-time backfill: coconut milk/cream are tinned (the canned-goods
        ``soup`` icon, Cupboard), but early adds catalogued them under the
        ``coconut`` -> ``apple`` fruit fallback. The ``icon IS NULL`` enrichment
        guard means the corrected map never re-applies to those rows, so rewrite
        the stored icon and category directly on catalog and items. Scoped to the
        ``apple`` fallback so a deliberately-set icon is untouched; idempotent (a
        second run finds no ``apple`` rows for these names)."""
        with self._lock:
            self._conn.execute(
                "UPDATE catalog SET icon = 'soup', category = 'Cupboard' "
                "WHERE name_norm IN ('coconut milk', 'coconut cream') "
                "AND icon = 'apple'"
            )
            self._conn.execute(
                "UPDATE items SET icon = 'soup', category = 'Cupboard' "
                "WHERE name_norm IN ('coconut milk', 'coconut cream') "
                "AND icon = 'apple'"
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
            builtin = self._builtin(name_norm)
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
        """Count an add against the catalogue, REVIVING a stashed row if this
        name was deleted inside the stash window.

        Revival is the whole trick, and it is the ``ON CONFLICT`` clause doing
        it: the stale-stash purge runs first, so by the time the upsert lands a
        row under this key either is a live shortcut, or is a stash still inside
        its window, or does not exist. The first two take the UPDATE branch —
        which clears ``retired_at``, so a stash returns with its count, its
        learned icon and its category intact — and the third inserts fresh at
        one. No branch in Python, and no window where a row is half-revived.

        The revived count goes UP by one, not back to what it was: the re-add
        really is an add, and nothing here can tell an undo from a shopper
        deciding two minutes later that they do want milk after all. Over 54
        shops the difference is noise; what the shopper actually notices —
        Milk staying at the top of the tray — is exact.

        Assumes the caller already holds ``self._lock`` and will commit —
        deliberately not a locked/committing method of its own so it can be part
        of a caller's larger atomic unit (see add_item)."""
        now = _now()
        self._purge_stale_stashes(now)
        self._conn.execute(
            "INSERT INTO catalog "
            "(name_norm, display_name, times_added, last_added) "
            "VALUES (?, ?, 1, ?) "
            "ON CONFLICT(name_norm) DO UPDATE SET "
            "times_added = times_added + 1, last_added = excluded.last_added, "
            "retired_at = NULL",
            (name_norm, display_name, now),
        )

    def _purge_stale_stashes(self, now: str) -> None:
        """Drop stashed catalogue rows whose window has passed.

        Housekeeping only — every read already filters ``retired_at IS NULL``,
        so a row that outlives its purge is invisible either way. What the purge
        buys is that the window ENDS: past it, ``_bump_catalog``'s upsert finds
        no row and inserts a fresh one at count one, which is what "the delete
        was final" has to mean.

        It cannot reap a row someone is about to revive. The cutoff is strict
        (``<``), and purge and revival run inside one lock hold off one ``now``,
        so a row inside its window at the moment of the check is still inside it
        at the moment of the upsert — there is no instant in between for another
        thread to take it.

        Same contract as _bump_catalog: caller holds the lock and commits."""
        cutoff = (
            datetime.fromisoformat(now) - timedelta(seconds=STASH_WINDOW_SECONDS)
        ).isoformat()
        self._conn.execute(
            "DELETE FROM catalog WHERE retired_at IS NOT NULL AND retired_at < ?",
            (cutoff,),
        )

    def get_item(self, item_id: str) -> dict | None:
        with self._lock:
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
        """Delete an item and RETIRE its catalogue row, whatever the count.
        Delete means "stop offering me this", full stop.

        The earlier version was cleverer: it decremented ``times_added`` and only
        dropped the row at zero, on the theory that deleting undoes one add and a
        staple should keep its history. That failed the first time it met a real
        user. They added a thing from the shortcut tray (count 1 → 2), swiped it
        off the list (2 → 1), and the shortcut was still sitting there. From
        where they sit they deleted it and it came back — and the original ask
        was exactly "when I delete an item it shouldn't appear in favourites any
        more, it might have been a bad transcription". A rule you have to explain
        with a counter loses to one you can see working.

        The row is STASHED rather than dropped: ``retired_at`` is stamped and
        every read filters it out, so the shortcut vanishes from the tray, from
        typeahead and from the MCP suggestions the instant the item goes — the
        original ask, unchanged, because invisible is invisible whatever is left
        on disk. What the stash buys is the swipe-delete's five-second undo,
        which re-adds by name through the normal add path: without it a stray
        swipe on Milk, caught and undone immediately, restored the item but
        silently dropped 54 shops of history to a fresh row at one, sending a
        staple to the bottom of the tray for weeks. An undo that presents as a
        full reversal and is not is worse than no undo. ``_bump_catalog`` revives
        a stash inside STASH_WINDOW_SECONDS; past that the purge takes it and the
        delete is final.

        Retiring is not decrementing. The count is untouched, so nothing here
        depends on a delete being the mirror of an add — the mistake the earlier
        version made.

        The rule applies whatever the row's STATUS. A per-row delete is a
        deliberate "get rid of this", and scoping it to active rows would leave a
        bad capture that happened to get checked off with no way to purge it at
        all. The "I bought these" gesture is ``clear_checked``, which empties the
        whole basket and deliberately touches nothing here.

        What this CANNOT reach is a name with no item row left at all — the
        ``name_norm`` is read off the item, so once the last one has been
        cleared away the shortcut can only be dropped by ``forget_catalog``.

        Once the stash ages out, the icon/category learned for that name goes
        with it; a later add re-learns from the built-in map (add_item's tier 0)
        or the LLM.

        The item delete and the catalogue retirement are one atomic unit: one
        lock hold, one commit. The piggy-backed purge rides the same unit —
        every delete is also the moment to sweep up stashes nobody came back
        for, which bounds the table to names deleted within the last hour
        without needing a timer anywhere.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT name_norm FROM items WHERE id = ?", (item_id,)
            ).fetchone()
            if row is None:
                return False
            now = _now()
            self._conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
            self._purge_stale_stashes(now)
            self._conn.execute(
                "UPDATE catalog SET retired_at = ? WHERE name_norm = ?",
                (now, row["name_norm"]),
            )
            self._conn.commit()
            return True

    def clear_checked(self) -> int:
        """Empty the basket. Emphatically NOT a delete in the catalogue sense:
        this is "I have bought all of these", so every times_added and last_added
        is left exactly as it was (a test pins that)."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM items WHERE status = 'checked'"
            )
            self._conn.commit()
            return cur.rowcount

    def list_items(self) -> dict:
        with self._lock:
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
        """The live catalogue keys — stashed ones deliberately excluded.

        These feed ``normalise``'s singular/plural fold, which only ever moves a
        name onto a key the catalogue already holds. A stash must not be one of
        those keys: leaving it in would let "Lemons", added an hour after the
        shopper deleted "Lemon", fold onto the deleted key and quietly revive it
        — a shortcut reappearing under a name they believe they got rid of,
        pulled there by a row no surface will show them. Naming behaviour has to
        be explicable from what is on screen.

        This does not weaken the undo, which is the case the stash exists for:
        undo re-adds the same display name, so it normalises to the same key
        with no fold involved and hits the stash head-on.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT name_norm FROM catalog WHERE retired_at IS NULL"
            ).fetchall()
        return {row["name_norm"] for row in rows}

    def catalog_entry(self, name_norm: str) -> dict | None:
        """The live shortcut for a name, or None — a stashed row reads as absent
        exactly like a deleted one, so no caller can offer what was deleted."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM catalog WHERE name_norm = ? AND retired_at IS NULL",
                (name_norm,),
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
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM catalog WHERE retired_at IS NULL"
            ).fetchall()
        return self._rank(rows)[:n]

    def catalog_search(self, q: str, limit: int = 8) -> list[dict]:
        pattern = f"%{q}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM catalog "
                "WHERE retired_at IS NULL AND (name_norm LIKE ? OR display_name LIKE ?)",
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

    def forget_catalog(self, name_norm: str) -> bool:
        """Drop a shortcut from the catalogue. True if a row went, False if
        there was nothing under that key.

        The gesture behind this is "stop offering me that": a mis-heard capture
        ("Marty Rice" for basmati rice) is catalogued the moment it is added,
        and until this existed nothing could take it back out. ``delete_item``
        does clear the shortcut, but it needs an item row to hang that delete
        on — it looks the ``name_norm`` up FROM the item. Status is irrelevant
        (it works on a checked row just as well as an active one); EXISTENCE is
        not. Once every item with that name has been bought and cleared away,
        there is no row left to delete and the bad shortcut is stranded for
        good. This is the way back in.

        Deliberately touches ITEMS not at all — the mirror image of
        ``delete_item``, which takes both. A shortcut and a thing in the
        trolley are different objects that happen to share a name: forgetting
        the "Papa Dums" shortcut while poppadoms are genuinely on this week's
        list must not delete the row the shopper is about to buy. The whole row goes,
        learned icon/category and count together — this is "this name is wrong",
        so adding the name again re-learns from scratch, which is the point.

        HARD delete, deliberately, where ``delete_item`` stashes. The two
        gestures mean different things: swiping an item off the list is about
        this week's trolley and comes with a five-second undo, so its catalogue
        row is worth keeping warm; forgetting a shortcut is a considered "this
        name is wrong", made from the tray, with no undo behind it and no
        history worth preserving. Retiring it with a never-revive flag would
        reach the same place by carrying a second kind of tombstone through
        every read, the revive branch and the purge — three places to get the
        permanence wrong. Deleting the row is the permanence, and there is
        nothing left to be wrong about. It also reaps a row that is currently
        stashed, so forgetting during the undo window makes the name permanent
        immediately rather than leaving something revivable behind.

        Single statement, so the lock hold and the commit are all it needs.
        """
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM catalog WHERE name_norm = ?", (name_norm,)
            )
            self._conn.commit()
            return cur.rowcount > 0
