DEFAULT_WALK_ORDER = [
    "Fruit & Veg", "Bakery", "Meat & Fish", "Dairy & Eggs", "Cupboard",
    "Frozen", "Drinks", "Household", "Pet", "Other",
]


def resolve_category(value: str | None, walk_order: list[str]) -> str:
    return value if value in walk_order else "Other"
