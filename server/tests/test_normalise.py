from trug.normalise import normalise


def test_lowercases_and_collapses_whitespace():
    assert normalise("  Oat   Milk ") == "oat milk"


def test_singularises_only_to_known_key():
    assert normalise("eggs", known={"egg"}) == "egg"
    assert normalise("eggs", known=set()) == "eggs"      # dumb on purpose
    assert normalise("hummus", known={"egg"}) == "hummus"  # never invent keys
