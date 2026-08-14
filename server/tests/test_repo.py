from datetime import datetime, timedelta, timezone

import pytest
import trug.repo as repo_module
from trug.normalise import tidy_name
from trug.repo import STASH_WINDOW_SECONDS as _WINDOW
from trug.repo import Repository


@pytest.fixture
def repo():
    return Repository(":memory:")


class _Clock:
    """A movable stand-in for the repository's wall clock, so a test can sit out
    the stash window without sleeping through it."""

    def __init__(self):
        self.offset = 0.0

    def advance(self, seconds: float) -> None:
        self.offset += seconds

    def now(self) -> str:
        return (
            datetime.now(timezone.utc) + timedelta(seconds=self.offset)
        ).isoformat()


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(repo_module, "_now", c.now)
    return c


def _catalog_rows(repo, name_norm: str) -> int:
    """Raw row count for a key, stashed rows included — the one thing the public
    surface deliberately cannot see."""
    with repo._lock:
        return repo._conn.execute(
            "SELECT COUNT(*) AS n FROM catalog WHERE name_norm = ?", (name_norm,)
        ).fetchone()["n"]


@pytest.mark.parametrize(
    "raw, tidied",
    [
        ("bbq sauce", "Bbq Sauce"),
        ("BBQ sauce", "BBQ Sauce"),
        ("iPhone charger", "iPhone Charger"),
        ("milk", "Milk"),
        ("  dog   food ", "Dog Food"),  # whitespace collapses, words tidy
        ("Already Title", "Already Title"),
        ("", ""),
    ],
)
def test_tidy_name_rule(raw, tidied):
    assert tidy_name(raw) == tidied


def test_tidy_name_is_idempotent():
    for raw in ("bbq sauce", "BBQ sauce", "iPhone charger", "milk"):
        once = tidy_name(raw)
        assert tidy_name(once) == once


def test_add_tidies_stored_name_and_display(repo):
    item, _ = repo.add_item(None, "bbq sauce", None, "pwa", "alice")
    assert item["name"] == "Bbq Sauce"
    assert repo.catalog_entry("bbq sauce")["display_name"] == "Bbq Sauce"


def test_tidy_does_not_break_dedup(repo):
    # name_norm keys off normalise (lowercased), so a differently-cased second
    # add still dedups onto the first despite the cosmetic tidy.
    a, created_a = repo.add_item(None, "bbq sauce", None, "pwa", "alice")
    b, created_b = repo.add_item(None, "BBQ SAUCE", "extra", "pwa", "alice")
    assert created_a and not created_b
    assert b["id"] == a["id"] and b["note"] == "extra"


def test_tidy_migration_idempotent(tmp_path):
    import sqlite3

    dbfile = tmp_path / "tidy.db"
    conn = sqlite3.connect(str(dbfile))
    conn.executescript(_OLD_SCHEMA)
    conn.execute(
        "INSERT INTO items (id, name, name_norm, status, created_at) "
        "VALUES ('a', 'bbq sauce', 'bbq sauce', 'active', '2020-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "CREATE TABLE catalog (name_norm TEXT PRIMARY KEY, display_name TEXT NOT NULL, "
        "icon TEXT, category TEXT, times_added INTEGER NOT NULL DEFAULT 0, last_added TEXT)"
    )
    conn.execute(
        "INSERT INTO catalog (name_norm, display_name, times_added) "
        "VALUES ('bbq sauce', 'bbq sauce', 1)"
    )
    conn.commit()
    conn.close()

    repo = Repository(str(dbfile))
    assert repo.list_items()["active"][0]["name"] == "Bbq Sauce"
    assert repo.catalog_entry("bbq sauce")["display_name"] == "Bbq Sauce"

    # Reopening again changes nothing (fixed point).
    repo2 = Repository(str(dbfile))
    assert repo2.list_items()["active"][0]["name"] == "Bbq Sauce"
    assert repo2.catalog_entry("bbq sauce")["display_name"] == "Bbq Sauce"


def test_add_and_list(repo):
    item, created = repo.add_item(None, "Milk", None, "pwa", "alice")
    assert created and item["status"] == "active" and item["name"] == "Milk"
    assert repo.list_items()["active"][0]["id"] == item["id"]


def test_add_is_idempotent_on_id(repo):
    a, _ = repo.add_item("0198a000-0000-7000-8000-000000000001", "Milk", None, "pwa", "alice")
    b, created = repo.add_item(a["id"], "Milk", None, "pwa", "alice")
    assert not created and b["id"] == a["id"]
    assert len(repo.list_items()["active"]) == 1


def test_dedup_by_name_norm_merges_note(repo):
    a, _ = repo.add_item(None, "milk", "semi", "pwa", "alice")
    b, created = repo.add_item(None, "  MILK ", "2 pints", "ring", None)
    assert not created and b["id"] == a["id"] and b["note"] == "semi; 2 pints"


def test_re_adding_checked_item_reactivates_it(repo):
    # New semantics (commit "re-adding a checked item reactivates it"): a checked
    # same-norm item is REACTIVATED on re-add rather than leaving a duplicate in
    # the basket. Supersedes the old "checked doesn't block a new add" rule.
    a, _ = repo.add_item(None, "milk", "semi", "pwa", "alice")
    repo.set_status(a["id"], "checked")
    assert repo.get_item(a["id"])["checked_at"] is not None
    times_before = repo.catalog_entry("milk")["times_added"]

    b, created = repo.add_item(None, "MILK", "2 pints", "ring", None)

    # Same row, reactivated in place — no second "Milk" appears.
    assert not created and b["id"] == a["id"]
    assert b["status"] == "active" and b["checked_at"] is None
    # Distinct note merged; catalog bumped (genuine re-add intent).
    assert b["note"] == "semi; 2 pints"
    assert repo.catalog_entry("milk")["times_added"] == times_before + 1
    assert len(repo.list_items()["active"]) == 1
    assert repo.list_items()["checked"] == []


def test_reactivate_does_not_duplicate_identical_note(repo):
    a, _ = repo.add_item(None, "coffee", "decaf", "pwa", "alice")
    repo.set_status(a["id"], "checked")
    b, created = repo.add_item(None, "coffee", "decaf", "pwa", "alice")
    assert not created and b["id"] == a["id"] and b["note"] == "decaf"


def test_reactivate_reactivates_most_recent_checked_dupe(repo):
    # Legacy data can hold several checked dupes; reactivate the most recent and
    # leave the others for the collapse migration to clean up. Insert the rows
    # directly (bypassing add_item) to reproduce that pre-migration state.
    conn = repo._conn
    conn.execute(
        "INSERT INTO items (id, name, name_norm, note, status, created_at, sort_key) "
        "VALUES ('old', 'Coffee', 'coffee', 'a', 'checked', '2020-01-01T00:00:00+00:00', 1)"
    )
    conn.execute(
        "INSERT INTO items (id, name, name_norm, note, status, created_at, sort_key) "
        "VALUES ('new', 'Coffee', 'coffee', 'b', 'checked', '2021-01-01T00:00:00+00:00', 2)"
    )
    conn.commit()
    item, created = repo.add_item(None, "coffee", None, "pwa", "alice")
    assert not created and item["id"] == "new" and item["status"] == "active"
    # The older checked dupe is untouched (migration territory).
    assert repo.get_item("old")["status"] == "checked"


def test_check_uncheck_clear(repo):
    a, _ = repo.add_item(None, "milk", None, "pwa", "alice")
    assert repo.set_status(a["id"], "checked")["checked_at"] is not None
    assert repo.clear_checked() == 1
    assert repo.get_item(a["id"]) is None


def test_catalog_frecency_and_singularisation(repo):
    for _ in range(3):
        i, _ = repo.add_item(None, "Egg", None, "pwa", "alice")
        repo.set_status(i["id"], "checked"); repo.clear_checked()
    repo.add_item(None, "Milk", None, "pwa", "alice")
    top = repo.catalog_top(2)
    assert top[0]["name_norm"] == "egg" and top[0]["times_added"] == 3
    # "eggs" normalises onto the known "egg" key
    item, _ = repo.add_item(None, "Eggs", None, "pwa", "alice")
    assert repo.catalog_entry("egg")["times_added"] == 4


def test_plural_then_singular_lands_on_one_row(repo):
    """The reported bug end-to-end: "add Lemons, then add Lemon, end up with two
    rows". add_item feeds normalise the CATALOGUE keys (known_norms), so the
    second add's "lemon" pluralises onto the established "lemons" key and dedups
    onto the existing row. The survivor is the FIRST spelling — the dedup branch
    returns the stored row untouched, so the list keeps showing "Lemons"."""
    a, created_a = repo.add_item(None, "Lemons", None, "pwa", "alice")
    b, created_b = repo.add_item(None, "Lemon", "waxed", "pwa", "alice")

    assert created_a and not created_b
    assert b["id"] == a["id"]
    assert b["name"] == "Lemons"          # first spelling wins the display name
    assert b["note"] == "waxed"           # the second add's note is absorbed
    assert len(repo.list_items()["active"]) == 1
    assert repo.known_norms() == {"lemons"}


def test_singular_then_plural_lands_on_one_row(repo):
    """The direction that already worked (rule 2, singularise onto a known key)
    must keep working: "Lemon" then "Lemons" is one row, displaying "Lemon"."""
    a, created_a = repo.add_item(None, "Lemon", None, "pwa", "alice")
    b, created_b = repo.add_item(None, "Lemons", None, "pwa", "alice")

    assert created_a and not created_b
    assert b["id"] == a["id"]
    assert b["name"] == "Lemon"
    assert len(repo.list_items()["active"]) == 1
    assert repo.known_norms() == {"lemon"}


def test_fold_happens_only_onto_a_catalogue_key(repo):
    """The fold never invents a key: a singular whose plural the catalogue does
    not hold stays exactly as typed, and gets its own row."""
    repo.add_item(None, "Lemons", None, "pwa", "alice")
    pear, created = repo.add_item(None, "Pear", None, "pwa", "alice")

    assert created and pear["name"] == "Pear"
    assert len(repo.list_items()["active"]) == 2
    assert repo.known_norms() == {"lemons", "pear"}
    assert "pears" not in repo.known_norms()


def test_asparagus_is_never_stemmed_to_asparagu(repo):
    """The `in known` guard, end-to-end: an s-final singular is not a plural.
    Adding it to a fresh list, and again to a list holding unrelated keys, must
    leave the catalogue key "asparagus" and never create "asparagu"."""
    repo.add_item(None, "Milk", None, "pwa", "alice")
    a, created = repo.add_item(None, "Asparagus", None, "pwa", "alice")
    assert created and a["name"] == "Asparagus"

    b, created_b = repo.add_item(None, "  ASPARAGUS ", None, "ring", None)
    assert not created_b and b["id"] == a["id"] and b["name"] == "Asparagus"

    assert "asparagu" not in repo.known_norms()
    assert repo.known_norms() == {"milk", "asparagus"}
    assert len(repo.list_items()["active"]) == 2


def test_a_catalogued_asparagu_typo_still_captures_asparagus(repo):
    """Known consequence of precedence rule 2, pinned so a change is deliberate:
    the guard only asks whether the stripped stem IS a catalogue key, so a
    mistyped "Asparagu" the household once added makes the next "Asparagus"
    fold onto the typo's row — the real word never gets a row of its own, and
    the list keeps showing "Asparagu". The reverse order is the happy one: with
    "asparagus" established, a later "Asparagu" pluralises back onto the real
    word (rule 3), so the typo is absorbed rather than given its own row."""
    typo, _ = repo.add_item(None, "Asparagu", None, "pwa", "alice")
    folded, created = repo.add_item(None, "Asparagus", None, "pwa", "alice")

    assert not created and folded["id"] == typo["id"]
    assert folded["name"] == "Asparagu"
    assert repo.known_norms() == {"asparagu"}

    # Reverse order: with the real word catalogued first, the typo folds onto it
    # (rule 3) instead of earning a second row.
    fresh = Repository(":memory:")
    real, _ = fresh.add_item(None, "Asparagus", None, "pwa", "alice")
    stray, created_stray = fresh.add_item(None, "Asparagu", None, "pwa", "alice")
    assert not created_stray and stray["id"] == real["id"]
    assert stray["name"] == "Asparagus"
    assert fresh.known_norms() == {"asparagus"}


def test_enrichment_written_once(repo):
    # "widget" is not a tier-0 builtin, so set_enrichment is the first writer.
    repo.add_item(None, "widget", None, "pwa", "alice")
    repo.set_enrichment("widget", "Widget", "soup", "Cupboard")
    repo.set_enrichment("widget", "Widget", "meat", "Frozen")  # ignored
    e = repo.catalog_entry("widget")
    assert e["icon"] == "soup" and e["category"] == "Cupboard"


def test_tier0_icon_applied_synchronously_on_add(repo):
    # No LLM anywhere: the built-in map fills icon + category at insert time.
    item, created = repo.add_item(None, "Milk", None, "pwa", "alice")
    assert created
    assert item["icon"] == "milk" and item["category"] == "Dairy & Eggs"
    # Catalog carries the same enrichment for future adds.
    e = repo.catalog_entry("milk")
    assert e["icon"] == "milk" and e["category"] == "Dairy & Eggs"


def test_tier0_substring_and_plural_on_add(repo):
    plural, _ = repo.add_item(None, "Bananas", None, "pwa", "alice")
    assert plural["icon"] == "banana" and plural["category"] == "Fruit & Veg"
    sub, _ = repo.add_item(None, "cherry tomatoes", None, "pwa", "alice")
    assert sub["icon"] == "apple" and sub["category"] == "Fruit & Veg"


def test_tier0_unknown_name_leaves_icon_none(repo):
    item, _ = repo.add_item(None, "Flux Capacitor", None, "pwa", "alice")
    assert item["icon"] is None and item["category"] is None


def test_tier0_does_not_override_existing_catalog_icon(repo):
    # A non-builtin name enriched by the LLM tier keeps that icon when the
    # item is checked, cleared, and re-added as a genuine insert.
    a, _ = repo.add_item(None, "quaver", None, "pwa", "alice")
    assert a["icon"] is None  # not a builtin
    repo.set_enrichment("quaver", "Quavers", "candy", "Cupboard")
    repo.set_status(a["id"], "checked")
    repo.clear_checked()
    b, created = repo.add_item(None, "quaver", None, "pwa", "alice")
    assert created and b["icon"] == "candy" and b["category"] == "Cupboard"
    assert repo.catalog_entry("quaver")["icon"] == "candy"


def test_missing_item_operations(repo):
    assert repo.set_status("nope", "checked") is None
    assert repo.delete_item("nope") is False


def test_update_item_sets_fields(repo):
    a, _ = repo.add_item(None, "milk", None, "pwa", "alice")
    updated = repo.update_item(a["id"], note="semi", category="Dairy & Eggs")
    assert updated["note"] == "semi" and updated["category"] == "Dairy & Eggs"
    assert repo.update_item("nope", note="x") is None


def test_dedup_does_not_bump_catalog(repo):
    repo.add_item(None, "milk", None, "pwa", "alice")
    assert repo.catalog_entry("milk")["times_added"] == 1
    # Re-adding an active duplicate must not bump the catalog.
    repo.add_item(None, "milk", None, "pwa", "alice")
    assert repo.catalog_entry("milk")["times_added"] == 1


def test_update_item_status_checked_sets_checked_at(repo):
    a, _ = repo.add_item(None, "milk", None, "pwa", "alice")
    updated = repo.update_item(a["id"], status="checked")
    assert updated["status"] == "checked" and updated["checked_at"] is not None
    reactivated = repo.update_item(a["id"], status="active")
    assert reactivated["status"] == "active" and reactivated["checked_at"] is None


# --- sort_key / drag-to-reorder ----------------------------------------


def test_new_item_gets_sort_key(repo):
    item, _ = repo.add_item(None, "milk", None, "pwa", "alice")
    assert isinstance(item["sort_key"], float)


def test_active_items_ordered_by_sort_key(repo):
    a, _ = repo.add_item(None, "apple", None, "pwa", "alice")
    b, _ = repo.add_item(None, "banana", None, "pwa", "alice")
    order = [i["name"] for i in repo.list_items()["active"]]
    assert order == ["Apple", "Banana"]  # insertion order via ascending sort_key
    # Drag apple below banana by handing it a larger sort_key.
    updated = repo.update_item(a["id"], sort_key=b["sort_key"] + 1)
    assert updated["sort_key"] == b["sort_key"] + 1
    order = [i["name"] for i in repo.list_items()["active"]]
    assert order == ["Banana", "Apple"]


_OLD_SCHEMA = """
CREATE TABLE items (
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
    checked_at TEXT
);
"""


def _make_dupe_db(tmp_path, name="dupe.db"):
    import sqlite3

    dbfile = tmp_path / name
    conn = sqlite3.connect(str(dbfile))
    conn.executescript(_OLD_SCHEMA)
    conn.execute(
        "CREATE TABLE catalog (name_norm TEXT PRIMARY KEY, display_name TEXT NOT NULL, "
        "icon TEXT, category TEXT, times_added INTEGER NOT NULL DEFAULT 0, last_added TEXT)"
    )
    # Two checked + one active "Coffee", plus a distinct-noted checked dupe.
    conn.execute(
        "INSERT INTO items (id, name, name_norm, note, status, created_at) "
        "VALUES ('c1', 'Coffee', 'coffee', 'decaf', 'checked', '2020-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO items (id, name, name_norm, note, status, created_at) "
        "VALUES ('c2', 'Coffee', 'coffee', 'beans', 'checked', '2021-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO items (id, name, name_norm, note, status, created_at) "
        "VALUES ('c3', 'Coffee', 'coffee', NULL, 'active', '2019-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()
    return dbfile


def test_migration_collapses_duplicate_norms(tmp_path):
    dbfile = _make_dupe_db(tmp_path)
    repo = Repository(str(dbfile))
    rows = repo.list_items()
    coffees = [i for i in rows["active"] + rows["checked"] if i["name"] == "Coffee"]
    # Collapsed to a single row — the active one wins as keeper.
    assert len(coffees) == 1
    keeper = coffees[0]
    assert keeper["id"] == "c3" and keeper["status"] == "active"
    # Distinct notes from the deleted dupes are merged into the keeper.
    assert keeper["note"] is not None
    assert set(keeper["note"].split("; ")) == {"decaf", "beans"}


def test_migration_collapse_is_idempotent(tmp_path):
    dbfile = _make_dupe_db(tmp_path)
    Repository(str(dbfile))
    repo2 = Repository(str(dbfile))
    rows = repo2.list_items()
    coffees = [i for i in rows["active"] + rows["checked"] if i["name"] == "Coffee"]
    assert len(coffees) == 1
    assert set(coffees[0]["note"].split("; ")) == {"decaf", "beans"}


def test_migration_adds_and_backfills_sort_key(tmp_path):
    import sqlite3

    dbfile = tmp_path / "old.db"
    conn = sqlite3.connect(str(dbfile))
    conn.executescript(_OLD_SCHEMA)
    # Two rows inserted "out of order": the one with the later created_at first.
    conn.execute(
        "INSERT INTO items (id, name, name_norm, status, created_at) "
        "VALUES ('b', 'Banana', 'banana', 'active', '2021-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO items (id, name, name_norm, status, created_at) "
        "VALUES ('a', 'Apple', 'apple', 'active', '2020-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    # Reopening with the current code migrates the schema in place.
    repo = Repository(str(dbfile))
    active = repo.list_items()["active"]
    assert all(i["sort_key"] is not None for i in active)
    # Backfill orders by created_at epoch, so Apple (older) precedes Banana.
    assert [i["name"] for i in active] == ["Apple", "Banana"]


def _make_nut_icon_db(tmp_path):
    import sqlite3

    dbfile = tmp_path / "nut.db"
    conn = sqlite3.connect(str(dbfile))
    conn.executescript(_OLD_SCHEMA)
    conn.execute(
        "CREATE TABLE catalog (name_norm TEXT PRIMARY KEY, display_name TEXT NOT NULL, "
        "icon TEXT, category TEXT, times_added INTEGER NOT NULL DEFAULT 0, last_added TEXT)"
    )
    conn.execute(
        "INSERT INTO catalog (name_norm, display_name, icon, category) "
        "VALUES ('pine kernels', 'Pine Kernels', 'nut', 'Cupboard')"
    )
    conn.execute(
        "INSERT INTO items (id, name, name_norm, icon, category, status, created_at) "
        "VALUES ('n1', 'Pine Kernels', 'pine kernels', 'nut', 'Cupboard', 'active', "
        "'2020-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()
    return dbfile


def test_migration_renames_nut_icon_to_acorn(tmp_path):
    dbfile = _make_nut_icon_db(tmp_path)
    repo = Repository(str(dbfile))
    assert repo.catalog_entry("pine kernels")["icon"] == "acorn"
    active = repo.list_items()["active"]
    assert [i["icon"] for i in active if i["name"] == "Pine Kernels"] == ["acorn"]


def test_migration_nut_icon_rename_is_idempotent(tmp_path):
    dbfile = _make_nut_icon_db(tmp_path)
    Repository(str(dbfile))
    repo2 = Repository(str(dbfile))
    assert repo2.catalog_entry("pine kernels")["icon"] == "acorn"


def _make_coconut_apple_db(tmp_path):
    import sqlite3

    dbfile = tmp_path / "coconut.db"
    conn = sqlite3.connect(str(dbfile))
    conn.executescript(_OLD_SCHEMA)
    conn.execute(
        "CREATE TABLE catalog (name_norm TEXT PRIMARY KEY, display_name TEXT NOT NULL, "
        "icon TEXT, category TEXT, times_added INTEGER NOT NULL DEFAULT 0, last_added TEXT)"
    )
    # Coconut milk catalogued under the old coconut->apple fruit fallback.
    conn.execute(
        "INSERT INTO catalog (name_norm, display_name, icon, category) "
        "VALUES ('coconut milk', 'Coconut Milk', 'apple', 'Fruit & Veg')"
    )
    conn.execute(
        "INSERT INTO items (id, name, name_norm, icon, category, status, created_at) "
        "VALUES ('c1', 'Coconut Milk', 'coconut milk', 'apple', 'Fruit & Veg', 'active', "
        "'2020-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()
    return dbfile


def test_migration_fixes_coconut_milk_from_apple_to_tin(tmp_path):
    dbfile = _make_coconut_apple_db(tmp_path)
    repo = Repository(str(dbfile))
    entry = repo.catalog_entry("coconut milk")
    assert (entry["icon"], entry["category"]) == ("soup", "Cupboard")
    active = repo.list_items()["active"]
    item = next(i for i in active if i["name"] == "Coconut Milk")
    assert (item["icon"], item["category"]) == ("soup", "Cupboard")


def test_migration_coconut_milk_icon_is_idempotent(tmp_path):
    dbfile = _make_coconut_apple_db(tmp_path)
    Repository(str(dbfile))
    repo2 = Repository(str(dbfile))
    assert repo2.catalog_entry("coconut milk")["icon"] == "soup"


def _make_unfiled_db(tmp_path, name="unfiled.db"):
    """A pre-Herbs & Spices database: spice-rack names stranded in ``Other``
    with no icon, alongside rows the backfill must not touch."""
    import sqlite3

    dbfile = tmp_path / name
    conn = sqlite3.connect(str(dbfile))
    conn.executescript(_OLD_SCHEMA)
    conn.execute(
        "CREATE TABLE catalog (name_norm TEXT PRIMARY KEY, display_name TEXT NOT NULL, "
        "icon TEXT, category TEXT, times_added INTEGER NOT NULL DEFAULT 0, last_added TEXT)"
    )

    def item(item_id, name, norm, icon, category):
        conn.execute(
            "INSERT INTO items (id, name, name_norm, icon, category, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', '2020-01-01T00:00:00+00:00')",
            (item_id, name, norm, icon, category),
        )

    # Placeable by the built-in map, stranded in Other / NULL with no icon.
    item("i1", "Thyme", "thyme", None, "Other")
    item("i2", "Cumin", "cumin", None, None)
    item("i3", "Chives", "chives", None, "Other")
    # Must be left alone:
    item("i4", "Paprika", "paprika", "salt", "Cupboard")  # deliberately filed
    item("i5", "Sage", "sage", "leaf", "Other")           # already has an icon
    item("i6", "Ferret Harness", "ferret harness", None, "Other")  # map knows nothing

    conn.execute(
        "INSERT INTO catalog (name_norm, display_name, icon, category, times_added, "
        "last_added) VALUES ('paprika', 'Paprika', 'salt', 'Cupboard', 3, "
        "'2020-01-01T00:00:00+00:00')"
    )
    # Catalogued (from an older map) but not currently on the list.
    conn.execute(
        "INSERT INTO catalog (name_norm, display_name, icon, category, times_added, "
        "last_added) VALUES ('cinnamon', 'Cinnamon', 'cookie', 'Cupboard', 2, "
        "'2020-01-01T00:00:00+00:00')"
    )
    # Learned for a name the built-in map has never heard of — never touched.
    conn.execute(
        "INSERT INTO catalog (name_norm, display_name, icon, category, times_added, "
        "last_added) VALUES ('ferret harness', 'Ferret Harness', 'paw', 'Pet', 1, "
        "'2020-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()
    return dbfile


def _by_id(repo):
    rows = repo.list_items()
    return {i["id"]: i for i in rows["active"] + rows["checked"]}


def test_migration_refiles_unfiled_items_the_builtin_map_can_place(tmp_path):
    items = _by_id(Repository(str(_make_unfiled_db(tmp_path))))
    assert (items["i1"]["icon"], items["i1"]["category"]) == ("leaf", "Herbs & Spices")
    assert (items["i2"]["icon"], items["i2"]["category"]) == ("salt", "Herbs & Spices")
    assert (items["i3"]["icon"], items["i3"]["category"]) == ("leaf", "Herbs & Spices")


def test_migration_leaves_filed_and_enriched_items_alone(tmp_path):
    items = _by_id(Repository(str(_make_unfiled_db(tmp_path))))
    # Filed under a real aisle by a human or the LLM: not our call to move.
    assert (items["i4"]["icon"], items["i4"]["category"]) == ("salt", "Cupboard")
    # Other, but already carries an icon — enrichment has had its say.
    assert (items["i5"]["icon"], items["i5"]["category"]) == ("leaf", "Other")
    # The map cannot place it, so it stays put.
    assert (items["i6"]["icon"], items["i6"]["category"]) == (None, "Other")


def test_migration_refile_does_not_fight_a_later_user_move(tmp_path):
    dbfile = _make_unfiled_db(tmp_path)
    repo = Repository(str(dbfile))
    # The user drags Thyme back out of the new aisle...
    repo.update_item("i1", category="Other")
    # ...and a reboot re-runs the migration, which must not drag it back.
    items = _by_id(Repository(str(dbfile)))
    assert items["i1"]["category"] == "Other"
    assert items["i1"]["icon"] == "leaf"


def test_migration_refile_is_idempotent(tmp_path):
    dbfile = _make_unfiled_db(tmp_path)
    Repository(str(dbfile))
    items = _by_id(Repository(str(dbfile)))
    assert (items["i1"]["icon"], items["i1"]["category"]) == ("leaf", "Herbs & Spices")
    assert (items["i4"]["icon"], items["i4"]["category"]) == ("salt", "Cupboard")


def test_migration_refiles_catalog_for_names_the_builtin_map_knows(tmp_path):
    repo = Repository(str(_make_unfiled_db(tmp_path)))
    entry = repo.catalog_entry("paprika")
    assert (entry["icon"], entry["category"]) == ("salt", "Herbs & Spices")
    # Learned knowledge for a name outside the built-in map is preserved.
    unknown = repo.catalog_entry("ferret harness")
    assert (unknown["icon"], unknown["category"]) == ("paw", "Pet")


def test_migration_makes_a_fresh_add_land_in_the_new_aisle(tmp_path):
    repo = Repository(str(_make_unfiled_db(tmp_path)))
    item, created = repo.add_item(None, "cinnamon", None, "web", None)
    # The stale-catalogue row was the thing pinning re-adds to Cupboard.
    assert created is True
    assert (item["icon"], item["category"]) == ("salt", "Herbs & Spices")


# --- deleting an item removes its catalogue row -------------------------


def test_delete_removes_the_catalog_row_whatever_the_count(repo):
    """Delete means "stop offering me this", full stop. A staple with a long
    history is no exception: the shortcut goes now, and rebuilds from the next
    shop."""
    for _ in range(3):
        item, _ = repo.add_item(None, "Milk", None, "pwa", "alice")
        repo.set_status(item["id"], "checked")
        repo.clear_checked()
    last, _ = repo.add_item(None, "Milk", None, "pwa", "alice")
    assert repo.catalog_entry("milk")["times_added"] == 4

    assert repo.delete_item(last["id"]) is True

    assert repo.catalog_entry("milk") is None
    assert [e["name_norm"] for e in repo.catalog_top()] == []


def test_delete_removes_the_catalog_row_of_a_one_off(repo):
    """The reported bug: a mis-heard capture added once must stop being offered
    as a shortcut the moment it is deleted."""
    item, _ = repo.add_item(None, "bag of tarragon", None, "ring", None)
    assert repo.catalog_entry("bag of tarragon")["times_added"] == 1

    repo.delete_item(item["id"])

    assert repo.catalog_entry("bag of tarragon") is None
    assert "bag of tarragon" not in repo.known_norms()
    assert [e["name_norm"] for e in repo.catalog_top()] == []
    assert repo.catalog_search("tarragon") == []


def test_a_bad_capture_stays_gone_after_the_window(repo, clock):
    """The bad-transcription case end to end: added once, deleted, invisible on
    every surface — and it stays invisible once the revival window has passed
    AND the stash has been reaped, with nothing re-adding it."""
    item, _ = repo.add_item(None, "bag of tarragon", None, "ring", None)
    repo.delete_item(item["id"])

    clock.advance(_WINDOW + 60)
    # Some unrelated add drives the purge; tarragon must not come back with it.
    repo.add_item(None, "Milk", None, "pwa", "alice")

    assert repo.catalog_entry("bag of tarragon") is None
    assert "bag of tarragon" not in repo.known_norms()
    assert [e["name_norm"] for e in repo.catalog_top()] == ["milk"]
    assert repo.catalog_search("tarragon") == []
    assert _catalog_rows(repo, "bag of tarragon") == 0


def test_undo_inside_the_window_restores_the_history(repo):
    """The headline case. A staple with 54 shops behind it, caught by a stray
    swipe and undone straight away, must come back as the staple it was — not at
    the bottom of the tray at times_added = 1. The re-add still counts as an add,
    so the count comes back one higher, not one lower."""
    for _ in range(53):
        item, _ = repo.add_item(None, "Milk", None, "pwa", "alice")
        repo.set_status(item["id"], "checked")
        repo.clear_checked()
    milk, _ = repo.add_item(None, "Milk", None, "pwa", "alice")
    before = repo.catalog_entry("milk")
    assert before["times_added"] == 54

    repo.delete_item(milk["id"])
    assert repo.catalog_entry("milk") is None  # invisible the moment it goes

    repo.add_item(None, "Milk", None, "pwa", "alice")  # the undo

    after = repo.catalog_entry("milk")
    assert after["times_added"] == 55
    assert after["display_name"] == before["display_name"]
    assert [e["name_norm"] for e in repo.catalog_top()] == ["milk"]


def test_undo_restores_the_learned_icon_and_category(repo):
    """Reviving the stash brings back what was LEARNED for the name, not just the
    count — including enrichment the built-in map could never re-derive."""
    item, _ = repo.add_item(None, "Marmite", None, "pwa", "alice")
    repo.set_enrichment("marmite", "Marmite", "jar", "Cupboard")
    assert repo.catalog_entry("marmite")["icon"] == "jar"

    repo.delete_item(item["id"])
    repo.add_item(None, "Marmite", None, "pwa", "alice")  # the undo

    entry = repo.catalog_entry("marmite")
    assert (entry["icon"], entry["category"]) == ("jar", "Cupboard")


def test_a_readd_after_the_window_starts_a_fresh_row(repo, clock):
    """Past the window the delete is final: the same name added again is a new
    shortcut starting at one, with the learned enrichment gone with the stash."""
    item, _ = repo.add_item(None, "Marmite", None, "pwa", "alice")
    repo.set_enrichment("marmite", "Marmite", "jar", "Cupboard")
    for _ in range(4):
        repo.set_status(item["id"], "checked")
        repo.clear_checked()
        item, _ = repo.add_item(None, "Marmite", None, "pwa", "alice")
    assert repo.catalog_entry("marmite")["times_added"] == 5

    repo.delete_item(item["id"])
    clock.advance(_WINDOW + 1)
    repo.add_item(None, "Marmite", None, "pwa", "alice")

    entry = repo.catalog_entry("marmite")
    assert entry["times_added"] == 1
    assert entry["icon"] is None and entry["category"] is None


def test_a_stash_is_still_revivable_at_the_edge_of_the_window(repo, clock):
    """The purge may never take a row someone can still revive: a stash one
    second inside the window survives an intervening purge-driving add."""
    milk, _ = repo.add_item(None, "Milk", None, "pwa", "alice")
    repo.delete_item(milk["id"])

    clock.advance(_WINDOW - 1)
    repo.add_item(None, "Bread", None, "pwa", "alice")  # drives the purge
    repo.add_item(None, "Milk", None, "pwa", "alice")  # the late undo

    assert repo.catalog_entry("milk")["times_added"] == 2


def test_the_purge_reaps_stashes_past_the_window(repo, clock):
    """Housekeeping: a stash nobody revived does not sit in the table forever."""
    item, _ = repo.add_item(None, "Kombucha", None, "pwa", "alice")
    repo.delete_item(item["id"])
    assert _catalog_rows(repo, "kombucha") == 1  # stashed, not yet gone

    clock.advance(_WINDOW + 1)
    repo.add_item(None, "Milk", None, "pwa", "alice")

    assert _catalog_rows(repo, "kombucha") == 0


def test_a_stashed_row_does_not_fold_new_names_onto_itself(repo, clock):
    """``known_norms`` feeds the singular/plural fold, so a stash must not exert
    gravity while it is invisible. Delete "Lemon" and adding "Lemons" is a plain
    new shortcut under its own key — folding it onto the deleted "lemon" would
    revive, under a name the shopper believes they got rid of, a row they cannot
    see to reason about. The undo path is unaffected: it re-adds the same display
    name, which normalises to the same key without any fold."""
    lemon, _ = repo.add_item(None, "Lemon", None, "pwa", "alice")
    repo.delete_item(lemon["id"])

    assert "lemon" not in repo.known_norms()
    plural, _ = repo.add_item(None, "Lemons", None, "pwa", "alice")

    assert repo.catalog_entry("lemons")["times_added"] == 1
    assert repo.catalog_entry("lemon") is None
    # ...and the stash is still just a stash: it ages out on its own.
    clock.advance(_WINDOW + 1)
    repo.add_item(None, "Bread", None, "pwa", "alice")
    assert _catalog_rows(repo, "lemon") == 0


def test_existing_db_gains_the_stash_column_and_keeps_its_shortcuts(tmp_path):
    """A live household's DB predates retired_at, and CREATE TABLE IF NOT EXISTS
    will not add it. The migration must, without disturbing rows that were never
    deleted — and the stash must work on the upgraded DB."""
    import sqlite3

    dbfile = tmp_path / "pre-stash.db"
    conn = sqlite3.connect(str(dbfile))
    conn.executescript(_OLD_SCHEMA)
    conn.execute(
        "CREATE TABLE catalog (name_norm TEXT PRIMARY KEY, display_name TEXT NOT NULL, "
        "icon TEXT, category TEXT, times_added INTEGER NOT NULL DEFAULT 0, last_added TEXT)"
    )
    conn.execute(
        "INSERT INTO catalog (name_norm, display_name, times_added, last_added) "
        "VALUES ('milk', 'Milk', 54, '2026-08-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    repo = Repository(str(dbfile))
    assert repo.catalog_entry("milk")["times_added"] == 54

    item, _ = repo.add_item(None, "Milk", None, "pwa", "alice")
    repo.delete_item(item["id"])
    assert repo.catalog_entry("milk") is None
    repo.add_item(None, "Milk", None, "pwa", "alice")  # the undo
    assert repo.catalog_entry("milk")["times_added"] == 56

    # Idempotent: a second open of the same file finds the column already there.
    assert Repository(str(dbfile)).catalog_entry("milk")["times_added"] == 56


def test_forget_is_permanent_and_not_revivable(repo):
    """``forget_catalog`` means "this name is WRONG", not "not now" — there is no
    undo affordance behind it, so it hard-deletes and a later add of the same
    name re-learns from scratch rather than reviving anything."""
    item, _ = repo.add_item(None, "Marty Rice", None, "ring", None)
    repo.set_enrichment("marty rice", "Marty Rice", "jar", "Cupboard")
    for _ in range(3):
        repo.set_status(item["id"], "checked")
        repo.clear_checked()
        item, _ = repo.add_item(None, "Marty Rice", None, "ring", None)
    assert repo.catalog_entry("marty rice")["times_added"] == 4
    repo.set_status(item["id"], "checked")
    repo.clear_checked()  # nothing on the list, so the re-add below is a real add

    assert repo.forget_catalog("marty rice") is True
    assert _catalog_rows(repo, "marty rice") == 0  # gone, not stashed

    repo.add_item(None, "Marty Rice", None, "ring", None)
    entry = repo.catalog_entry("marty rice")
    assert entry["times_added"] == 1
    # Re-learned from the built-in map rather than restored: the forgotten row
    # carried "jar", and a forget takes the learned icon with it.
    assert entry["icon"] == "bowl"


def test_forget_also_reaps_a_stash(repo):
    """Forgetting a name that is currently stashed makes it permanent
    immediately, rather than leaving a revivable row behind."""
    item, _ = repo.add_item(None, "Marty Rice", None, "ring", None)
    repo.delete_item(item["id"])

    assert repo.forget_catalog("marty rice") is True

    repo.add_item(None, "Marty Rice", None, "ring", None)
    assert repo.catalog_entry("marty rice")["times_added"] == 1


def test_undo_of_a_removed_row_relearns_the_builtin_icon(repo):
    """Removing the row loses the learned icon/category. For a name the built-in
    map knows, the undo's re-add re-learns it, so the round trip is complete."""
    item, _ = repo.add_item(None, "Milk", None, "pwa", "alice")
    assert (item["icon"], item["category"]) == ("milk", "Dairy & Eggs")
    repo.delete_item(item["id"])
    again, _ = repo.add_item(None, "Milk", None, "pwa", "alice")
    assert (again["icon"], again["category"]) == ("milk", "Dairy & Eggs")
    entry = repo.catalog_entry("milk")
    assert (entry["icon"], entry["category"]) == ("milk", "Dairy & Eggs")


def test_delete_of_a_checked_item_removes_the_row_too(repo):
    """Per-row delete means "I did not mean this" whatever the row's status —
    otherwise a bad capture that happened to get checked off could never be
    purged. The bulk basket clear is the "I bought these" gesture, and it is the
    one that leaves the catalogue alone (see below)."""
    item, _ = repo.add_item(None, "Milk", None, "pwa", "alice")
    repo.set_status(item["id"], "checked")
    repo.delete_item(item["id"])
    assert repo.catalog_entry("milk") is None


def test_clear_checked_never_touches_the_catalog(repo):
    """Emptying the basket is "I have bought all of these" — the opposite of a
    delete. It must leave every count and last_added exactly as it found them."""
    for name in ("Milk", "Eggs", "Bread"):
        item, _ = repo.add_item(None, name, None, "pwa", "alice")
        repo.set_status(item["id"], "checked")
    before = {e["name_norm"]: dict(e) for e in repo.catalog_top()}
    assert repo.clear_checked() == 3
    after = {e["name_norm"]: dict(e) for e in repo.catalog_top()}
    assert after == before


def test_deleting_same_norm_rows_twice_is_harmless(repo):
    """Two list rows can share a norm (a legacy duplicate, or a re-add from
    another device racing an SSE echo). The first delete takes the catalogue row;
    the second finds nothing to take and still reports the item deleted."""
    repo.add_item(None, "Milk", None, "pwa", "alice")
    with repo._lock:
        repo._conn.execute(
            "INSERT INTO items (id, name, name_norm, status, created_at) "
            "VALUES ('dupe', 'Milk', 'milk', 'active', '2020-01-01T00:00:00+00:00')"
        )
        repo._conn.commit()
    item_ids = [i["id"] for i in repo.list_items()["active"]]
    assert [repo.delete_item(i) for i in item_ids] == [True, True]
    assert repo.catalog_entry("milk") is None
    assert repo.list_items()["active"] == []
