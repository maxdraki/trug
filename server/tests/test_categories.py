from trug.categories import DEFAULT_WALK_ORDER, resolve_category


def test_default_order_ends_with_other():
    assert DEFAULT_WALK_ORDER[0] == "Fruit & Veg"
    assert DEFAULT_WALK_ORDER[-1] == "Other"


def test_herbs_and_spices_sits_directly_after_cupboard():
    # Herbs and spices are cooking ingredients: in a UK shop they are shelved
    # with the ambient grocery block, immediately after the Cupboard staples and
    # before the chilled/frozen leg of the walk.
    assert DEFAULT_WALK_ORDER.index("Herbs & Spices") == (
        DEFAULT_WALK_ORDER.index("Cupboard") + 1
    )
    assert DEFAULT_WALK_ORDER.index("Herbs & Spices") < DEFAULT_WALK_ORDER.index("Frozen")


def test_unknown_category_resolves_to_other():
    assert resolve_category("Fishmongery", DEFAULT_WALK_ORDER) == "Other"
    assert resolve_category(None, DEFAULT_WALK_ORDER) == "Other"
    assert resolve_category("Frozen", DEFAULT_WALK_ORDER) == "Frozen"
