import re
from pathlib import Path

import pytest

from trug.categories import DEFAULT_WALK_ORDER
from trug.icons import BUILTIN, ICON_VOCABULARY, lookup
from trug.repo import Repository

CLIENT_ICONS_TS = Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "icons.ts"


def _client_icon_slugs() -> set[str] | None:
    """Every slug the PWA has a glyph for, read out of the generated icon map.

    None when there is no `web/` beside this checkout — a missing tree is not a
    drift, so the test that needs this skips rather than fails. (Same shape as
    web/src/lib/walkOrder.test.ts, which reads the server's walk order from the
    other direction.)
    """
    try:
        source = CLIENT_ICONS_TS.read_text(encoding="utf-8")
    except OSError:
        return None
    return set(re.findall(r"^\s*'([^']+)':", source, re.MULTILINE))


CLIENT_ICON_SLUGS = _client_icon_slugs()

# What "has an icon" is worth asserting AGAINST. `ICON_VOCABULARY` is derived
# from BUILTIN, so `lookup(...)[0] in ICON_VOCABULARY` is true by construction —
# it stayed green with a key pointed at a slug ("rocket-ship") that exists
# nowhere in either language. The client map is the set that can actually be
# missing one, and a slug missing from it renders as a blank chip.
ICON_GLYPHS = CLIENT_ICON_SLUGS if CLIENT_ICON_SLUGS is not None else set(ICON_VOCABULARY)


def test_every_icon_slug_has_a_glyph_in_the_client_map():
    # The one invariant that spans the two languages: `item.icon` is a slug the
    # server chose, and the PWA draws it by looking that slug up in a GENERATED
    # map (web/scripts/gen-icons.mjs parses this very module). Add a slug here
    # without regenerating and every item carrying it renders an empty chip —
    # nothing on either side would otherwise notice.
    if CLIENT_ICON_SLUGS is None:
        pytest.skip(
            f"no web tree beside this one ({CLIENT_ICONS_TS}) — nothing to check the icon "
            "vocabulary against. Run this from a full checkout."
        )
    assert len(CLIENT_ICON_SLUGS) >= 40, "icons.ts parsed suspiciously empty"
    missing = sorted(set(ICON_VOCABULARY) - CLIENT_ICON_SLUGS)
    assert not missing, (
        f"{missing} have no glyph in {CLIENT_ICONS_TS.name}: "
        "run `node scripts/gen-icons.mjs > src/lib/icons.ts` in web/"
    )


def test_lookup_exact_match():
    assert lookup("milk") == ("milk", "Dairy & Eggs")
    assert lookup("bread") == ("bread", "Bakery")


def test_lookup_plural_strips_to_singular():
    # "eggs" is not a key; the trailing-s fallback reaches "egg".
    assert "eggs" not in BUILTIN
    assert lookup("eggs") == BUILTIN["egg"]
    assert lookup("bananas") == BUILTIN["banana"]


def test_lookup_plural_ies_folds_onto_the_y_singular():
    # The map is kept singular, and "-y" nouns pluralise as "-ies", so the
    # trailing-"s" fold alone left the everyday spelling unreachable: nobody
    # writes "nappy" or "strawberry" on a list.
    for plural, singular in [
        ("nappies", "nappy"),
        ("ice lollies", "ice lolly"),
        ("strawberries", "strawberry"),
        ("cherries", "cherry"),
        ("raspberries", "raspberry"),
        ("blueberries", "blueberry"),
        ("batteries", "battery"),
        ("pastries", "pastry"),
        ("whiskies", "whisky"),
    ]:
        assert plural not in BUILTIN, plural
        assert lookup(plural) == BUILTIN[singular], plural


def test_the_ies_fold_never_invents_a_key():
    # Same discipline as the trailing-"s" fold: the folded form has to already
    # BE a key, so a naive "-ies -> -y" rewrite cannot misfile anything. Without
    # that guard "brownies" becomes "browny" and "anchovies" "anchovy".
    assert lookup("brownies") is None
    assert lookup("anchovies") is None
    # …and the plain trailing-"s" fold is tried first, so an "-ie" singular is
    # never mangled into a "-y" one.
    assert lookup("smoothies") == BUILTIN["smoothie"]
    assert lookup("cookies") == BUILTIN["cookie"]


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
        assert slug in ICON_GLYPHS, (name, slug)


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
        assert slug in ICON_GLYPHS, (name, slug)


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


PAPER = ("toilet-paper", "Household")


def test_every_name_for_loo_paper_lands_on_the_same_shelf():
    # The owner's rule: "Loo Paper" = "Toilet Paper". Whatever anyone in the
    # house calls it, and whichever side of the Atlantic the word comes from,
    # it is one product with one icon and one aisle.
    for name in [
        "loo paper", "loo papers", "loo roll", "loo rolls",
        "toilet paper", "toilet roll", "toilet rolls",
        "bog roll", "bog rolls",
        "toilet tissue", "bathroom tissue",
    ]:
        assert lookup(name) == PAPER, name


def test_bog_roll_is_not_a_bread_roll():
    # "roll" (4) is a Bakery key sitting inside "bog roll", which used to file
    # the loo paper with the baps.
    assert lookup("bog roll") == PAPER
    # …and the bakery keeps its own rolls.
    assert lookup("roll")[1] == "Bakery"
    assert lookup("bread rolls")[1] == "Bakery"
    assert lookup("cinnamon rolls")[1] == "Bakery"
    assert lookup("kitchen roll") == PAPER


# Each pair is one product with two names — a British one and an American one —
# and the aisle both of them belong in. The aisle is the third column because
# agreement alone is worth little: moving BOTH spellings to Frozen kept every
# assertion here green, and around thirty of these pairs have their category
# pinned nowhere else in the file.
TRANSATLANTIC_PAIRS = [
    ("washing up liquid", "dish soap", "Household"),
    ("washing-up liquid", "dishwashing liquid", "Household"),
    ("kitchen roll", "paper towels", "Household"),
    ("kitchen towel", "paper towel", "Household"),
    ("cling film", "plastic wrap", "Household"),
    ("clingfilm", "saran wrap", "Household"),
    ("tin foil", "aluminum foil", "Household"),
    ("kitchen foil", "aluminium foil", "Household"),
    ("courgette", "zucchini", "Fruit & Veg"),
    ("courgettes", "zucchinis", "Fruit & Veg"),
    ("aubergine", "eggplant", "Fruit & Veg"),
    ("coriander", "cilantro", "Fruit & Veg"),
    ("rocket", "arugula", "Fruit & Veg"),
    ("swede", "rutabaga", "Fruit & Veg"),
    ("spring onion", "scallion", "Fruit & Veg"),
    ("spring onions", "green onions", "Fruit & Veg"),
    ("prawns", "shrimp", "Meat & Fish"),
    ("mince", "ground beef", "Meat & Fish"),
    ("mince", "ground meat", "Meat & Fish"),
    ("beetroot", "beets", "Fruit & Veg"),
    ("nappy", "diaper", "Household"),
    ("bin bags", "trash bags", "Household"),
    ("bin liner", "garbage bag", "Household"),
    ("ice lolly", "popsicle", "Frozen"),
    ("porridge", "oatmeal", "Cupboard"),
    ("sweets", "candy", "Cupboard"),
    ("plaster", "band aid", "Medicines"),
    ("plasters", "band-aid", "Medicines"),
    ("cotton buds", "cotton swabs", "Household"),
    ("cotton bud", "q tip", "Household"),
    ("tomato puree", "tomato paste", "Cupboard"),
    ("bicarbonate of soda", "baking soda", "Cupboard"),
    ("crisps", "potato chips", "Cupboard"),
    ("tea towel", "dish towel", "Household"),
]


def test_british_and_american_names_resolve_identically():
    for british, american, category in TRANSATLANTIC_PAIRS:
        hit = lookup(british)
        assert hit is not None, british
        # The aisle each pair belongs in, not merely the one they agree on.
        assert hit[1] == category, (british, hit)
        assert lookup(american) == hit, (british, american)


def test_transatlantic_keys_do_not_hijack_their_namesakes():
    # Every key added for the vocabulary fix, scanned against the everyday
    # phrases it now sits inside or is outranked by.
    # "wrap" (Bakery) inside "plastic wrap"/"saran wrap".
    assert lookup("plastic wrap")[1] == "Household"
    assert lookup("wraps")[1] == "Bakery"
    assert lookup("tortilla wraps")[1] == "Bakery"
    # "sponge" (Household) inside the cake.
    assert lookup("sponge cake")[1] == "Bakery"
    assert lookup("victoria sponge")[1] == "Bakery"
    assert lookup("sponges")[1] == "Household"
    # "soap" (bottle) is the hand soap; the sink stuff is the spray.
    assert lookup("dish soap") == lookup("washing up liquid")
    assert lookup("hand soap") == ("bottle", "Household")
    # Bare "soda" is deliberately not a key: it lives inside all three of these.
    assert lookup("baking soda")[1] == "Cupboard"
    assert lookup("soda bread")[1] == "Bakery"
    assert lookup("soda water")[1] == "Drinks"
    # "tea" (Cupboard) inside the tea towel; "egg" inside the aubergine.
    assert lookup("tea towel")[1] == "Household"
    assert lookup("tea bags")[1] == "Cupboard"
    assert lookup("eggplant")[1] == "Fruit & Veg"
    assert lookup("eggs")[1] == "Dairy & Eggs"
    # "beet" must not steal the beetroot, nor "candy" the candle.
    assert lookup("beetroot") == lookup("beets")
    assert lookup("candy floss")[1] == "Cupboard"
    assert lookup("candles")[1] == "Household"
    # "potato" (Fruit & Veg) inside the American word for crisps; the chips in
    # the freezer are untouched either way.
    assert lookup("potato chips")[1] == "Cupboard"
    assert lookup("potatoes")[1] == "Fruit & Veg"
    assert lookup("oven chips")[1] == "Frozen"
    assert lookup("french fries")[1] == "Frozen"
    assert lookup("fries")[1] == "Frozen"
    # "tomato" (Fruit & Veg) inside the tin of paste.
    assert lookup("tomato paste")[1] == "Cupboard"
    assert lookup("tomatoes")[1] == "Fruit & Veg"


def test_poppadoms_resolve_in_every_spelling_the_shops_use():
    # UK supermarkets cannot agree on the spelling, so all of them are keys.
    # Aisle: poppadoms are sold boxed in world foods / with the crisps, not at
    # the bakery counter, so they file under Cupboard with the crackers — the
    # fresh flatbreads (naan, chapati, roti, paratha) stay in Bakery.
    for name in [
        "poppadom", "poppadoms", "poppadum", "poppadums", "poppadam",
        "poppadams", "papadom", "papadum", "papadums", "pappadam",
        "pappadams", "pappadum", "pappadums", "papadam", "papadams",
    ]:
        assert lookup(name) == ("cookie", "Cupboard"), name


def test_indian_flatbreads_shelve_with_the_naan():
    for name in ["naan", "naan bread", "chapati", "chapatis", "chapatti",
                 "chapattis", "roti", "rotis", "paratha", "parathas"]:
        assert lookup(name) == ("bread", "Bakery"), name


def test_zaatar_survives_its_apostrophe():
    # Punctuation is not stripped by normalise, so the key carries it. The
    # unpunctuated spelling is its own key rather than a lucky substring hit.
    assert lookup("za'atar") == ("salt", HERBS)
    assert lookup("zaatar") == ("salt", HERBS)


def test_uk_staples_that_used_to_return_nothing():
    expected = {
        # (name, category) — the icon is pinned by the aisle tests above.
        "tonic": "Drinks",
        "tonic water": "Drinks",
        "ribena": "Drinks",
        "lucozade": "Drinks",
        "cous cous": "Cupboard",
        "pesto": "Cupboard",
        "chutney": "Cupboard",
        "mango chutney": "Cupboard",
        "worcestershire sauce": "Cupboard",
        "worcester sauce": "Cupboard",
        "stock pot": "Cupboard",
        "gnocchi": "Cupboard",
        "ravioli": "Cupboard",
        "tortellini": "Cupboard",
        "ramen": "Cupboard",
        "cornstarch": "Cupboard",
        "digestives": "Bakery",
        "hobnobs": "Bakery",
        "rich tea": "Bakery",
        "custard creams": "Bakery",
        "shortbread": "Bakery",
        "salami": "Meat & Fish",
        "pepperoni": "Meat & Fish",
        "chorizo": "Meat & Fish",
        "prosciutto": "Meat & Fish",
        "black pudding": "Meat & Fish",
        "limescale remover": "Household",
    }
    for name, category in expected.items():
        hit = lookup(name)
        assert hit is not None, name
        assert hit[1] == category, (name, hit)
        assert hit[0] in ICON_GLYPHS, (name, hit)


def test_uk_staple_keys_do_not_hijack_their_namesakes():
    # "lime" (4) was sending the descaler to the citrus.
    assert lookup("limescale remover")[1] == "Household"
    assert lookup("limes")[1] == "Fruit & Veg"
    # "pepper" (6) was filing the charcuterie in the veg rack, and "pepperoni"
    # (9) must not then drag the pizza out of the freezer.
    assert lookup("pepperoni")[1] == "Meat & Fish"
    assert lookup("peppers")[1] == "Fruit & Veg"
    assert lookup("pepperoni pizza") == ("pizza", "Frozen")
    # "corn" (4) was filing the cornflour substitute with the sweetcorn.
    assert lookup("cornstarch") == lookup("cornflour")
    assert lookup("sweetcorn")[1] == "Fruit & Veg"
    # Biscuits hidden inside dairy and bakery keys: "custard" (7), "cream" (5),
    # "bread" (5), "tea" (3, below the floor but the phrase resolved to nothing).
    assert lookup("custard creams") == lookup("digestives")
    assert lookup("custard")[1] == "Dairy & Eggs"
    assert lookup("rich tea")[1] == "Bakery"
    assert lookup("tea bags")[1] == "Cupboard"
    assert lookup("rich tea biscuits")[1] == "Bakery"
    # "garlic" (6) beat "bread" (5) and put the Friday-night loaf in the veg.
    assert lookup("garlic bread")[1] == "Bakery"
    assert lookup("garlic")[1] == "Fruit & Veg"
    assert lookup("garlic cloves")[1] == "Fruit & Veg"
    # "mango" (5) was filing the jar of chutney with the fruit.
    assert lookup("mango chutney")[1] == "Cupboard"
    assert lookup("mangoes")[1] == "Fruit & Veg"
    # "water" (5) ties with "tonic" (5) and wins on position; the mixer is a
    # bottle in Drinks either way, and still bottled, not the still-water icon.
    assert lookup("tonic water") == lookup("tonic")


# --- The regression net for the longest-substring rule --------------------
#
# CORRECTION. The commit message for the vocabulary sweep (35271d1) claimed "a
# script now enumerates every key-that-contains-another-key pair in the map and
# checks the longer phrase wins; all hundred of them do". Read carefully that
# claim is worthless, and no test in this file ever implemented it:
#
#   * A key-inside-key pair is safe BY CONSTRUCTION. Both strings are keys, so
#     tier 0 of lookup() is an exact dict hit and the substring rule is never
#     consulted. Enumerating those pairs asserts a tautology, which is why the
#     only mechanical loop over BUILTIN here (the SINGULAR/PLURAL fold check
#     below) tests something else entirely.
#   * The failure mode that actually bites is an UNLISTED phrase built from a
#     short key plus ordinary words — "toilet duck", "nappy cream", "wrapping
#     paper", "digestive enzymes". None of those is a key, so no enumeration
#     over the map can generate them, and no amount of looping will find the
#     next one.
#
# There is no honest mechanical substitute: the input space is English, not the
# key set. So the net is a corpus instead — real phrases a UK household writes,
# each pinned to the aisle it belongs in. It is hand-built and therefore
# incomplete, and that is the point: when a new key misfiles a phrase, the phrase
# gets added here, and the file records what has actually been checked rather
# than implying the whole space has been.
PHRASE_CORPUS = [
    # Short key + ordinary word, where the wrong key used to win.
    ("toilet duck", "Household"),          # "duck" -> Meat & Fish
    ("nappy cream", "Medicines"),          # "cream" -> Dairy & Eggs
    ("nappy rash cream", "Medicines"),
    ("wrapping paper", "Household"),       # "wrap" -> Bakery
    ("bubble wrap", "Household"),
    ("gift wrap", "Household"),
    ("digestive enzymes", "Medicines"),    # "digestive" -> Bakery
    ("rotisserie chicken", "Meat & Fish"),  # "roti" -> Bakery
    ("rotisserie", "Meat & Fish"),
    ("hand cream", "Household"),
    ("shaving cream", "Household"),
    ("chocolate cake", "Bakery"),          # "chocolate" -> Cupboard
    ("carrot cake", "Bakery"),             # "carrot" -> Fruit & Veg
    ("sausage rolls", "Bakery"),           # "sausage" -> Meat & Fish
    ("strawberry jam", "Cupboard"),        # "strawberry" -> Fruit & Veg
    ("raspberry jam", "Cupboard"),
    ("free range eggs", "Dairy & Eggs"),   # "egg" is under the substring floor
    # The everyday shop, aisle by aisle.
    ("cherry tomatoes", "Fruit & Veg"),
    ("baby spinach", "Fruit & Veg"),
    ("red onions", "Fruit & Veg"),
    ("garlic cloves", "Fruit & Veg"),
    ("new potatoes", "Fruit & Veg"),
    ("sweet potatoes", "Fruit & Veg"),
    ("seedless grapes", "Fruit & Veg"),
    ("fresh basil", "Fruit & Veg"),
    ("mixed salad", "Fruit & Veg"),
    ("strawberries", "Fruit & Veg"),
    ("garlic bread", "Bakery"),
    ("wholemeal bread", "Bakery"),
    ("birthday cake", "Bakery"),
    ("pain au chocolat", "Bakery"),
    ("digestive biscuits", "Bakery"),
    ("hot cross buns", "Bakery"),
    ("pastries", "Bakery"),
    ("chicken breasts", "Meat & Fish"),
    ("smoked salmon", "Meat & Fish"),
    ("pork chops", "Meat & Fish"),
    ("beef mince", "Meat & Fish"),
    ("streaky bacon", "Meat & Fish"),
    ("greek yoghurt", "Dairy & Eggs"),
    ("double cream", "Dairy & Eggs"),
    ("mature cheddar", "Dairy & Eggs"),
    ("salted butter", "Dairy & Eggs"),
    ("oat milk", "Dairy & Eggs"),
    ("baked beans", "Cupboard"),
    ("olive oil", "Cupboard"),
    ("basmati rice", "Cupboard"),
    ("peanut butter", "Cupboard"),
    ("chopped tomatoes", "Cupboard"),
    ("tea bags", "Cupboard"),
    ("instant coffee", "Cupboard"),
    ("cream crackers", "Cupboard"),
    ("mango chutney", "Cupboard"),
    ("potato chips", "Cupboard"),
    ("frozen peas", "Frozen"),
    ("oven chips", "Frozen"),
    ("vanilla ice cream", "Frozen"),
    ("ice lollies", "Frozen"),
    ("pepperoni pizza", "Frozen"),
    ("orange juice", "Drinks"),
    ("sparkling water", "Drinks"),
    ("red wine", "Drinks"),
    ("tonic water", "Drinks"),
    ("energy drinks", "Drinks"),
    ("black pepper", "Herbs & Spices"),
    ("chilli flakes", "Herbs & Spices"),
    ("bay leaves", "Herbs & Spices"),
    ("ground cumin", "Herbs & Spices"),
    ("sea salt", "Herbs & Spices"),
    ("mixed herbs", "Herbs & Spices"),
    ("kitchen roll", "Household"),
    ("washing up liquid", "Household"),
    ("bin bags", "Household"),
    ("fabric softener", "Household"),
    ("toilet cleaner", "Household"),
    ("oven cleaner", "Household"),
    ("shower gel", "Household"),
    ("nappies", "Household"),
    ("baby wipes", "Household"),
    ("aluminium foil", "Household"),
    ("light bulbs", "Household"),
    ("aa batteries", "Household"),
    ("dishwasher tablets", "Household"),
    ("cough sweets", "Medicines"),
    ("sun cream", "Medicines"),
    ("eye drops", "Medicines"),
    ("hay fever tablets", "Medicines"),
    ("cod liver oil", "Medicines"),
    ("throat lozenges", "Medicines"),
    ("dog food", "Pet"),
    ("cat litter", "Pet"),
    ("bird seed", "Pet"),
]


def test_realistic_phrases_land_in_the_aisle_a_shopper_expects():
    misfiled = []
    for phrase, category in PHRASE_CORPUS:
        hit = lookup(phrase)
        if hit is None or hit[1] != category:
            misfiled.append((phrase, category, hit))
    assert not misfiled, misfiled


def test_every_corpus_phrase_gets_a_real_icon():
    for phrase, _category in PHRASE_CORPUS:
        hit = lookup(phrase)
        assert hit is not None, phrase
        assert hit[0] in ICON_GLYPHS, (phrase, hit)


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
