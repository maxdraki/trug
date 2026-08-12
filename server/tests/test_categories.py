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


def test_medicines_sits_in_the_non_food_block_after_household():
    # The pharmacy counter is part of the non-food end of a UK shop: paracetamol
    # and plasters are picked on the same leg as the bin bags and the shampoo,
    # not on the food legs. Medicines therefore sits directly after Household —
    # and still ahead of Pet, which is the last real aisle before the catch-all.
    assert DEFAULT_WALK_ORDER.index("Medicines") == (
        DEFAULT_WALK_ORDER.index("Household") + 1
    )
    assert DEFAULT_WALK_ORDER.index("Medicines") < DEFAULT_WALK_ORDER.index("Pet")
    assert DEFAULT_WALK_ORDER.index("Drinks") < DEFAULT_WALK_ORDER.index("Medicines")


def test_unknown_category_resolves_to_other():
    assert resolve_category("Fishmongery", DEFAULT_WALK_ORDER) == "Other"
    assert resolve_category(None, DEFAULT_WALK_ORDER) == "Other"
    assert resolve_category("Frozen", DEFAULT_WALK_ORDER) == "Frozen"
