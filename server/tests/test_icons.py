from trug.categories import DEFAULT_WALK_ORDER
from trug.icons import BUILTIN, ICON_VOCABULARY, lookup
from trug.repo import Repository


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


HERBS = "Herbs & Spices"


def test_herbs_and_spices_resolve_offline_with_an_icon():
    # No-key mode leans entirely on this map: every one of these must come back
    # with a real icon slug AND the herbs-and-spices aisle, no LLM involved.
    for name in [
        "oregano", "thyme", "cumin", "sage", "dill", "chives", "tarragon",
        "rosemary", "bay leaves", "bay leaf", "mixed herbs", "italian herbs",
        "dried basil", "dried coriander", "dried parsley", "dried mint",
        "paprika", "smoked paprika", "turmeric", "cinnamon", "nutmeg",
        "ground ginger", "chilli powder", "chilli flakes", "cayenne pepper",
        "curry powder", "garam masala", "cardamom", "cloves", "star anise",
        "saffron", "vanilla", "vanilla extract", "black pepper", "peppercorns",
        "sea salt", "celery salt", "mustard seeds", "fennel seeds", "caraway",
        "allspice", "coriander seeds", "cumin seeds", "salt",
    ]:
        hit = lookup(name)
        assert hit is not None, name
        slug, category = hit
        assert category == HERBS, (name, category)
        assert slug in ICON_VOCABULARY, (name, slug)


def test_fresh_herbs_stay_in_fruit_and_veg():
    # Rule: bare name follows the dominant UK purchase form; an explicit
    # "fresh …" is always produce and an explicit "dried …" always a spice.
    for name in ["basil", "coriander", "parsley", "mint", "fresh herbs",
                 "fresh thyme", "fresh rosemary", "fresh sage", "fresh dill",
                 "fresh chives", "fresh tarragon", "fresh oregano"]:
        assert lookup(name)[1] == "Fruit & Veg", name


def test_ginger_root_versus_ground():
    assert lookup("ginger")[1] == "Fruit & Veg"
    assert lookup("root ginger")[1] == "Fruit & Veg"
    assert lookup("fresh ginger")[1] == "Fruit & Veg"
    assert lookup("ground ginger")[1] == HERBS


def test_black_pepper_does_not_collide_with_bell_peppers():
    assert lookup("pepper") == ("pepper", "Fruit & Veg")
    assert lookup("peppers") == ("pepper", "Fruit & Veg")
    assert lookup("red pepper")[1] == "Fruit & Veg"
    assert lookup("black pepper")[1] == HERBS
    assert lookup("white pepper")[1] == HERBS
    assert lookup("peppercorn")[1] == HERBS


def test_chilli_fresh_versus_dried():
    assert lookup("chilli")[1] == "Fruit & Veg"
    assert lookup("chillies")[1] == "Fruit & Veg"
    assert lookup("chilli flakes")[1] == HERBS
    assert lookup("dried chilli")[1] == HERBS


def test_fennel_seed_versus_bulb():
    # Bare "fennel" is the spice jar; the vegetable needs the explicit "bulb".
    assert lookup("fennel")[1] == HERBS
    assert lookup("fennel bulb")[1] == "Fruit & Veg"


def test_seed_spices_beat_their_ambient_namesakes():
    assert lookup("mustard") == ("bottle", "Cupboard")  # the condiment
    assert lookup("mustard seeds")[1] == HERBS
    assert lookup("celery")[1] == "Fruit & Veg"
    assert lookup("celery salt")[1] == HERBS
    assert lookup("coriander")[1] == "Fruit & Veg"
    assert lookup("coriander seeds")[1] == HERBS
    # "sage" is short enough to substring-match; "sausage" must not become a herb.
    assert lookup("sausages")[1] == "Meat & Fish"
    assert lookup("pork sausage")[1] == "Meat & Fish"
    # "clove" must not steal garlic cloves.
    assert lookup("garlic cloves")[1] == "Fruit & Veg"


def test_flavour_modifiers_do_not_outrank_the_product():
    # The longest-key rule lets a long flavour word beat a shorter head noun.
    # These are the everyday UK list entries where that misfiles the item.
    assert lookup("vanilla yogurt") == ("milk", "Dairy & Eggs")
    assert lookup("vanilla yoghurt") == ("milk", "Dairy & Eggs")
    assert lookup("vanilla yogurts") == ("milk", "Dairy & Eggs")
    assert lookup("cinnamon swirl")[1] == "Bakery"
    assert lookup("cinnamon buns")[1] == "Bakery"
    assert lookup("cinnamon rolls")[1] == "Bakery"
    # "rum" is below the four-character substring floor, so "spice" wins alone.
    assert lookup("spiced rum")[1] == "Drinks"
    assert lookup("peppercorn sauce")[1] == "Cupboard"


def test_short_herb_keys_do_not_hijack_longer_words():
    # "herb" is a 4-char key living inside unrelated grocery words.
    assert lookup("herbal tea") == ("cup", "Cupboard")
    assert lookup("herbal teas")[1] == "Cupboard"
    assert lookup("sherbet")[1] == "Cupboard"
    # Still a herb when it really is one.
    assert lookup("mixed herbs")[1] == HERBS
    assert lookup("dried herbs")[1] == HERBS


def test_baking_staples_stay_in_the_cupboard():
    # Salt and flavourings move to the seasoning shelf; bulk baking goods and
    # raising agents stay put.
    for name in ["sugar", "flour", "baking powder", "yeast"]:
        assert lookup(name)[1] == "Cupboard", name


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


NARROW_WALK_ORDER = ["Fruit & Veg", "Bakery", "Cupboard", "Other"]


def test_builtin_category_is_gated_by_the_configured_walk_order():
    # A household whose config omits "Herbs & Spices" must never have that
    # aisle invented for it: the tier-0 category goes through resolve_category
    # exactly as an LLM-assigned one does. The ICON is not gated — only the
    # category is — so the item still gets its glyph.
    repo = Repository(":memory:", walk_order=NARROW_WALK_ORDER)
    item, _created = repo.add_item(
        id=None, name="thyme", note=None, source="web", added_by=None
    )
    assert item["category"] == "Other"
    assert item["icon"] == "leaf"
    # A category the household does configure is untouched.
    item, _created = repo.add_item(
        id=None, name="bananas", note=None, source="web", added_by=None
    )
    assert item["category"] == "Fruit & Veg"


def test_builtin_category_defaults_to_the_default_walk_order():
    repo = Repository(":memory:")
    item, _created = repo.add_item(
        id=None, name="thyme", note=None, source="web", added_by=None
    )
    assert item["category"] == HERBS


def test_builtin_backfill_is_gated_by_the_configured_walk_order(tmp_path):
    db = tmp_path / "trug.db"
    repo = Repository(db, walk_order=NARROW_WALK_ORDER)
    repo._conn.execute(
        "INSERT INTO items (id, name, name_norm, status, created_at) "
        "VALUES ('x', 'Thyme', 'thyme', 'active', '2026-01-01T00:00:00+00:00')"
    )
    repo._conn.execute(
        "INSERT INTO catalog (name_norm, display_name) VALUES ('thyme', 'Thyme')"
    )
    repo._conn.commit()
    repo._conn.close()

    reopened = Repository(db, walk_order=NARROW_WALK_ORDER)
    row = reopened._conn.execute(
        "SELECT icon, category FROM items WHERE id = 'x'"
    ).fetchone()
    assert (row["icon"], row["category"]) == ("leaf", "Other")
    cat = reopened._conn.execute(
        "SELECT icon, category FROM catalog WHERE name_norm = 'thyme'"
    ).fetchone()
    assert (cat["icon"], cat["category"]) == ("leaf", "Other")


def test_builtin_has_broad_coverage():
    # ~250 name keys across a coherent (~60 slug) vocabulary.
    assert len(BUILTIN) >= 240
    assert 40 <= len(ICON_VOCABULARY) <= 120


MEDICINES = "Medicines"


def test_medicines_resolve_offline_with_an_icon():
    # No-key mode leans entirely on this map: the pharmacy run must come back
    # with a real icon slug AND the Medicines aisle, no LLM involved.
    for name in [
        "nasal spray", "paracetamol", "ibuprofen", "aspirin", "painkillers",
        "pain relief", "plasters", "bandages", "antiseptic", "cough syrup",
        "cough medicine", "throat lozenges", "cold and flu", "cold & flu",
        "decongestant", "antihistamine", "hay fever tablets", "hayfever",
        "indigestion tablets", "indigestion", "antacid", "rennies", "gaviscon",
        "lemsip", "calpol", "vitamins", "vitamin d", "multivitamins",
        "cod liver oil", "fish oil", "ibuprofen gel", "savlon", "germolene",
        "sudocrem", "tcp", "eye drops", "ear drops", "insect repellent",
        "after sun", "aftersun", "sun cream", "suncream", "sunscreen",
        "thermometer", "hand sanitiser", "hand sanitizer", "first aid kit",
        "contact lens solution", "contact lenses", "condoms", "pregnancy test",
        "period pain tablets", "rehydration sachets", "laxative", "imodium",
    ]:
        hit = lookup(name)
        assert hit is not None, name
        slug, category = hit
        assert category == MEDICINES, (name, category)
        assert slug in ICON_VOCABULARY, (name, slug)


def test_nasal_spray_is_not_dragged_into_the_cleaning_cupboard():
    # "spray" is the Household shelf-label slug and lives inside three cleaning
    # keys; the medicine must not follow it under the sink.
    assert lookup("nasal spray")[1] == MEDICINES
    assert lookup("nasal sprays")[1] == MEDICINES
    # …and the cleaning sprays stay exactly where they were.
    assert lookup("surface spray")[1] == "Household"
    assert lookup("cleaning spray")[1] == "Household"
    assert lookup("air freshener")[1] == "Household"


def test_sun_cream_beats_the_dairy_cream_keys():
    # "cream" (5) is a Dairy key sitting inside "suncream", which used to file
    # factor 50 with the double cream.
    for name in ["sun cream", "suncream", "sunscreen", "sun screen",
                 "sun lotion", "after sun", "aftersun", "factor 50 sun cream"]:
        assert lookup(name)[1] == MEDICINES, name
    # The dairy aisle is untouched, and neither is the freezer.
    assert lookup("cream")[1] == "Dairy & Eggs"
    assert lookup("double cream")[1] == "Dairy & Eggs"
    assert lookup("single cream")[1] == "Dairy & Eggs"
    assert lookup("soured cream")[1] == "Dairy & Eggs"
    assert lookup("cream cheese")[1] == "Dairy & Eggs"
    assert lookup("ice cream")[1] == "Frozen"
    # Medicinal creams are the other half of the same collision.
    assert lookup("antiseptic cream")[1] == MEDICINES
    assert lookup("sudocrem")[1] == MEDICINES


def test_cough_and_cold_remedies_beat_the_confectionery_keys():
    # "sweets"/"sweet" are Cupboard keys living inside the pharmacy phrases.
    assert lookup("cough sweets")[1] == MEDICINES
    assert lookup("cough drops")[1] == MEDICINES
    assert lookup("throat lozenges")[1] == MEDICINES
    assert lookup("cold and flu")[1] == MEDICINES
    assert lookup("cold and flu tablets")[1] == MEDICINES
    # …and the sweet counter keeps its own.
    assert lookup("sweets")[1] == "Cupboard"
    assert lookup("sherbet")[1] == "Cupboard"
    assert lookup("chewing gum")[1] == "Cupboard"


def test_medicine_keys_do_not_hijack_their_grocery_namesakes():
    # Short new keys scanned against the phrases they sit inside. Bare "syrup",
    # "cream", "drop", "oil", "tablet" and "spray" are deliberately NOT keys.
    assert lookup("maple syrup") is None or lookup("maple syrup")[1] != MEDICINES
    assert lookup("golden syrup") is None or lookup("golden syrup")[1] != MEDICINES
    assert lookup("dishwasher tablets")[1] == "Household"
    assert lookup("cod")[1] == "Meat & Fish"
    assert lookup("salmon fillets")[1] == "Meat & Fish"
    assert lookup("olive oil")[1] == "Cupboard"
    assert lookup("salad dressing")[1] == "Cupboard"
    assert lookup("shower gel")[1] == "Household"
    # "ear drop" hides inside "pear drops", and "vitamin" inside the drink.
    assert lookup("pear drops")[1] == "Cupboard"
    assert lookup("vitamin water")[1] == "Drinks"
    assert lookup("hand soap")[1] == "Household"
    assert lookup("mouthwash")[1] == "Household"
    assert lookup("toothpaste")[1] == "Household"
    # "cod liver oil" and "fish oil" are the supplements, not the fish counter.
    assert lookup("cod liver oil")[1] == MEDICINES
    assert lookup("fish oil")[1] == MEDICINES


def test_tissues_stay_in_household_with_the_paper_goods():
    # A box of tissues is shelved with the kitchen roll and the loo roll, not at
    # the pharmacy counter — it is a paper good that happens to be handy in a
    # cold. Cotton wool and cotton buds stay put for the same reason.
    assert lookup("tissues") == ("toilet-paper", "Household")
    assert lookup("tissue")[1] == "Household"
    assert lookup("kleenex")[1] == "Household"
    assert lookup("cotton wool")[1] == "Household"
    assert lookup("cotton buds")[1] == "Household"
    # Period products stay with the toiletries; only the painkiller moves.
    assert lookup("tampons")[1] == "Household"
    assert lookup("sanitary towels")[1] == "Household"
    assert lookup("period pain tablets")[1] == MEDICINES


def test_no_builtin_key_folds_onto_an_unrelated_aisle():
    # normalise() folds a trailing "s" in both directions against known names.
    # A key whose singular/plural is a DIFFERENT builtin key in a DIFFERENT
    # aisle would make an add land on the wrong shelf depending only on which
    # spelling the household typed first.
    for key, (_slug, category) in BUILTIN.items():
        for other in (key + "s", key[:-1] if key.endswith("s") else None):
            if other is None or other not in BUILTIN:
                continue
            assert BUILTIN[other][1] == category, (key, other)


def test_backfill_leaves_a_filed_household_row_where_the_owner_put_it(tmp_path):
    # The Medicines aisle arrived after "Nasal Spray" was already sitting in
    # Household WITH an icon. The one-time backfill only touches rows that are
    # visibly unfiled (catch-all category AND no icon), so this row stays put —
    # moving someone's live list under them is not ours to do. The CATALOG entry
    # is re-stamped, so the NEXT add of that name lands in Medicines.
    db = tmp_path / "trug.db"
    repo = Repository(db)
    repo._conn.execute(
        "INSERT INTO items (id, name, name_norm, status, category, icon, created_at) "
        "VALUES ('n', 'Nasal Spray', 'nasal spray', 'active', 'Household', "
        "'spray', '2026-01-01T00:00:00+00:00')"
    )
    repo._conn.execute(
        "INSERT INTO catalog (name_norm, display_name, icon, category) "
        "VALUES ('nasal spray', 'Nasal Spray', 'spray', 'Household')"
    )
    repo._conn.commit()
    repo._conn.close()

    reopened = Repository(db)
    row = reopened._conn.execute(
        "SELECT icon, category FROM items WHERE id = 'n'"
    ).fetchone()
    assert (row["icon"], row["category"]) == ("spray", "Household")
    cat = reopened._conn.execute(
        "SELECT icon, category FROM catalog WHERE name_norm = 'nasal spray'"
    ).fetchone()
    assert (cat["icon"], cat["category"]) == ("spray", "Medicines")
