from trug.categories import DEFAULT_WALK_ORDER
from trug.icons import BUILTIN, ICON_VOCABULARY, lookup


def test_lookup_exact_match():
    assert lookup("milk") == ("milk", "Dairy & Eggs")
    assert lookup("bread") == ("bread", "Bakery")


def test_lookup_plural_strips_to_singular():
    # "eggs" is not a key; the trailing-s fallback reaches "egg".
    assert "eggs" not in BUILTIN
    assert lookup("eggs") == BUILTIN["egg"]
    assert lookup("bananas") == BUILTIN["banana"]


def test_lookup_substring_containment():
    assert lookup("semi skimmed milk") == ("milk", "Dairy & Eggs")
    # Head-noun wins over the modifier on an equal-length tie: "tomato" (later)
    # beats "cherry", so the tomato entry is returned, not the cherry one.
    assert lookup("cherry tomatoes") == BUILTIN["tomato"]
    assert lookup("cherry tomatoes") != BUILTIN["cherry"]


def test_coconut_milk_is_a_tin_not_a_fruit():
    # The longest-key rule would resolve "coconut milk" to the coconut->apple
    # fruit fallback; the explicit entries put the tinned products in Cupboard
    # with the canned-goods icon instead.
    assert lookup("coconut milk") == ("soup", "Cupboard")
    assert lookup("coconut cream") == ("soup", "Cupboard")


def test_lookup_min_length_miss():
    # "oil" (3 chars) is a real key but too short to substring-match, and
    # nothing else is contained, so an unknown word returns None.
    assert lookup("boiler") is None
    assert lookup("wibble") is None


def test_lookup_unknown_returns_none():
    assert lookup("flux capacitor") is None


def test_all_builtin_categories_are_walk_order():
    for name, (_emoji, category) in BUILTIN.items():
        assert category in DEFAULT_WALK_ORDER, (name, category)


def test_builtin_covers_every_walk_order_category():
    used = {category for _slug, category in BUILTIN.values()}
    # Every shelf except the "Other" catch-all should have staples.
    for category in DEFAULT_WALK_ORDER:
        if category == "Other":
            continue
        assert category in used, category


def test_icon_vocabulary_is_sorted_unique_and_covers_builtin():
    assert ICON_VOCABULARY == sorted(set(ICON_VOCABULARY))
    used = {slug for slug, _category in BUILTIN.values()}
    assert set(ICON_VOCABULARY) == used


def test_builtin_has_broad_coverage():
    # ~250 name keys across a coherent (~60 slug) vocabulary.
    assert len(BUILTIN) >= 240
    assert 40 <= len(ICON_VOCABULARY) <= 120
