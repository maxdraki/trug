import pytest
from trug.normalise import tidy_name
from trug.repo import Repository


@pytest.fixture
def repo():
    return Repository(":memory:")


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
