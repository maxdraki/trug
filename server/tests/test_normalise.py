from trug.normalise import normalise


def test_lowercases_and_collapses_whitespace():
    assert normalise("  Oat   Milk ") == "oat milk"


def test_singularises_only_to_known_key():
    assert normalise("eggs", known={"egg"}) == "egg"
    assert normalise("eggs", known=set()) == "eggs"      # dumb on purpose
    assert normalise("hummus", known={"egg"}) == "hummus"  # never invent keys


def test_pluralises_onto_a_known_plural_key():
    """The mirror of the singularising fold: a new singular lands on the
    established plural key, so "lemons" then "lemon" is one item, not two."""
    assert normalise("lemon", known={"lemons"}) == "lemons"
    assert normalise("lemon", known=set()) == "lemon"
    assert normalise("lemon", known={"lime"}) == "lemon"  # never invent keys


def test_fold_is_order_independent():
    """Whichever spelling the household adds first becomes the catalogue key,
    and the other spelling folds onto it — one item either way."""
    # Singular added first: catalogue key is "lemon".
    first = normalise("Lemon", known=set())
    assert normalise("Lemons", known={first}) == first

    # Plural added first: catalogue key is "lemons".
    first = normalise("Lemons", known=set())
    assert normalise("Lemon", known={first}) == first


def test_exact_match_wins_when_both_keys_are_known():
    """Legacy databases carry both keys (the bug this fixes created them).
    An exact hit on an existing catalogue key must never be folded away, or
    that row becomes permanently unreachable by name."""
    both = {"lemon", "lemons"}
    assert normalise("lemons", known=both) == "lemons"
    assert normalise("lemon", known=both) == "lemon"


def test_s_final_singulars_survive_both_folds():
    """The `in known` guard is load-bearing: naive s-stripping would mangle
    real words, and naive s-appending would invent keys."""
    for word in ("asparagus", "couscous", "hummus", "sea bass"):
        assert normalise(word, known=set()) == word
        assert normalise(word, known={"egg", "milk"}) == word
        # Already catalogued: an exact hit stays put.
        assert normalise(word, known={word}) == word

    # Even if some other row happens to be the stripped stem, an exact
    # catalogue hit on the real word still wins.
    assert normalise("asparagus", known={"asparagus", "asparagu"}) == "asparagus"


def test_idiomatically_plural_groceries_keep_their_plural_key():
    """icons.BUILTIN keys these plural; folding a bare singular onto the
    established plural key keeps the icon/category lookup working."""
    for plural in ("peas", "oats", "beans", "nuts", "chips", "crisps",
                   "sweets", "matches", "cornflakes"):
        assert normalise(plural, known={plural}) == plural
        assert normalise(plural[:-1], known={plural}) == plural


def test_nothing_folds_without_a_known_counterpart():
    assert normalise("lemons", known={"bananas"}) == "lemons"
    assert normalise("lemon", known={"banana"}) == "lemon"
    assert normalise("", known={"s"}) == ""


def test_punctuation_is_carried_through_untouched():
    """Apostrophes and hyphens are part of the key: normalise lowercases and
    collapses whitespace and does nothing else. Names like "za'atar" and
    "washing-up liquid" must survive intact so the icon map (whose keys carry
    the same punctuation) can find them, and the plural fold must not trip
    over the punctuation either."""
    from trug.icons import lookup

    assert normalise("  Za'atar ") == "za'atar"
    assert normalise("Washing-Up Liquid") == "washing-up liquid"
    assert lookup(normalise("Za'atar")) == lookup("za'atar") is not None
    assert lookup(normalise("Washing-Up Liquid"))[1] == "Household"
    # The folds still behave: exact catalogue hit wins, plural folds on.
    assert normalise("za'atar", known={"za'atar"}) == "za'atar"
    assert normalise("chapatis", known={"chapati"}) == "chapati"
    # …and a punctuated name with no counterpart is left alone.
    assert normalise("za'atar", known={"zaatar"}) == "za'atar"


def test_normalise_is_pure_and_leaves_known_untouched():
    known = {"lemons"}
    snapshot = set(known)
    assert normalise("Lemon", known=known) == "lemons"
    assert normalise("Lemon", known=known) == "lemons"  # same answer twice
    assert known == snapshot
