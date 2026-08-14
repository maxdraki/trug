"""Enrichment tier 0: a built-in name -> (icon_slug, category) map.

Applied synchronously at add time, before/without any LLM call, so common
UK-household groceries get a real line icon and aisle offline, free, instantly.

Icons are monochrome line icons from the Tabler vocabulary
(https://tabler.io/icons); values are Tabler slugs, verified against
``@tabler/icons`` 3.x. Many-to-one is deliberate — a small, coherent vocabulary
(all cheeses -> ``cheese``, every fizzy drink -> ``bottle``) reads better than
per-item uniqueness. ``ICON_VOCABULARY`` is the sorted unique slug list, shared
by the client build and the LLM enrich prompt.

Keys are ``name_norm`` values (lowercase, whitespace-collapsed) kept singular
so the plural-stripping fallback in :func:`lookup` reaches them. Category values
are exact ``DEFAULT_WALK_ORDER`` strings.

Punctuation is part of the key. ``normalise()`` lowercases and collapses
whitespace but leaves hyphens and apostrophes alone, so a name people write both
ways needs BOTH spellings listed — the rule governs entries in four aisles
("q tip"/"q-tip" and "washing up liquid"/"washing-up liquid" in Household,
"band aid"/"band-aid"/"bandaid" in Medicines, "za'atar"/"zaatar" in Herbs &
Spices, "cling film"/"clingfilm" in Household) and is the first thing to check
when a name resolves for one household and not another.

British and American names for one product are both keys, mapped to the same
``(slug, category)`` pair — "loo paper" is "toilet paper" is "bog roll", and the
list should not care which the household types. (They stay separate ROWS: this
map only settles the icon and the aisle, not identity.) Where the two dialects
genuinely disagree — biscuit, chips, jelly, pudding — no synonym is asserted.
"""

from __future__ import annotations

FRUIT_VEG = "Fruit & Veg"
BAKERY = "Bakery"
MEAT_FISH = "Meat & Fish"
DAIRY = "Dairy & Eggs"
CUPBOARD = "Cupboard"
HERBS = "Herbs & Spices"
FROZEN = "Frozen"
DRINKS = "Drinks"
HOUSEHOLD = "Household"
MEDICINES = "Medicines"
PET = "Pet"
OTHER = "Other"

BUILTIN: dict[str, tuple[str, str]] = {
    # --- Fruit & Veg --------------------------------------------------
    "apple": ("apple", FRUIT_VEG),
    "banana": ("banana", FRUIT_VEG),
    "orange": ("lemon", FRUIT_VEG),
    "satsuma": ("lemon", FRUIT_VEG),
    "clementine": ("lemon", FRUIT_VEG),
    "grapefruit": ("lemon", FRUIT_VEG),
    "lemon": ("lemon", FRUIT_VEG),
    "lime": ("lemon", FRUIT_VEG),
    "grape": ("grape", FRUIT_VEG),
    "raisin": ("grape", FRUIT_VEG),
    "strawberry": ("cherry", FRUIT_VEG),
    "raspberry": ("cherry", FRUIT_VEG),
    "blueberry": ("cherry", FRUIT_VEG),
    "blackberry": ("cherry", FRUIT_VEG),
    "cherry": ("cherry", FRUIT_VEG),
    "plum": ("cherry", FRUIT_VEG),
    "fig": ("cherry", FRUIT_VEG),
    "melon": ("apple", FRUIT_VEG),
    "watermelon": ("apple", FRUIT_VEG),
    "pineapple": ("apple", FRUIT_VEG),
    "mango": ("apple", FRUIT_VEG),
    "peach": ("apple", FRUIT_VEG),
    "nectarine": ("apple", FRUIT_VEG),
    "pear": ("apple", FRUIT_VEG),
    "kiwi": ("apple", FRUIT_VEG),
    "coconut": ("apple", FRUIT_VEG),
    "date": ("apple", FRUIT_VEG),
    "avocado": ("avocado", FRUIT_VEG),
    "tomato": ("apple", FRUIT_VEG),
    "carrot": ("carrot", FRUIT_VEG),
    "parsnip": ("carrot", FRUIT_VEG),
    "potato": ("plant", FRUIT_VEG),
    "sweet potato": ("plant", FRUIT_VEG),
    "turnip": ("plant", FRUIT_VEG),
    "swede": ("plant", FRUIT_VEG),
    "rutabaga": ("plant", FRUIT_VEG),
    "beetroot": ("plant", FRUIT_VEG),
    # US "beets". Shorter than "beetroot", which is an exact key, so it cannot
    # steal it; nothing else in the map contains "beet".
    "beet": ("plant", FRUIT_VEG),
    "radish": ("plant", FRUIT_VEG),
    "onion": ("plant", FRUIT_VEG),
    "spring onion": ("plant", FRUIT_VEG),
    "scallion": ("plant", FRUIT_VEG),
    "shallot": ("plant", FRUIT_VEG),
    "garlic": ("plant", FRUIT_VEG),
    "leek": ("plant", FRUIT_VEG),
    # Bare "ginger" is the root in the veg rack; "ground ginger" is the jar and
    # lives in Herbs & Spices (below).
    "ginger": ("plant", FRUIT_VEG),
    "root ginger": ("plant", FRUIT_VEG),
    "fresh ginger": ("plant", FRUIT_VEG),
    "peas": ("plant", FRUIT_VEG),
    "green bean": ("plant", FRUIT_VEG),
    "sweetcorn": ("plant", FRUIT_VEG),
    "corn": ("plant", FRUIT_VEG),
    "butternut squash": ("plant", FRUIT_VEG),
    "pumpkin": ("plant", FRUIT_VEG),
    "broccoli": ("salad", FRUIT_VEG),
    "cauliflower": ("salad", FRUIT_VEG),
    "cabbage": ("salad", FRUIT_VEG),
    "lettuce": ("salad", FRUIT_VEG),
    "spinach": ("salad", FRUIT_VEG),
    "kale": ("salad", FRUIT_VEG),
    "celery": ("salad", FRUIT_VEG),
    "asparagus": ("salad", FRUIT_VEG),
    "brussels sprout": ("salad", FRUIT_VEG),
    "rhubarb": ("salad", FRUIT_VEG),
    "salad": ("salad", FRUIT_VEG),
    "cucumber": ("salad", FRUIT_VEG),
    "courgette": ("salad", FRUIT_VEG),
    "zucchini": ("salad", FRUIT_VEG),
    "aubergine": ("salad", FRUIT_VEG),
    # "egg" (3) is below the substring floor, so the aubergine never reached
    # Dairy — but the American name needs a key of its own to resolve at all.
    "eggplant": ("salad", FRUIT_VEG),
    "rocket": ("salad", FRUIT_VEG),
    "arugula": ("salad", FRUIT_VEG),
    "pepper": ("pepper", FRUIT_VEG),
    "chilli": ("pepper", FRUIT_VEG),
    "mushroom": ("mushroom", FRUIT_VEG),
    "fennel bulb": ("salad", FRUIT_VEG),
    # Fresh-vs-dried rule: a bare herb name follows the dominant UK purchase
    # form (these four are sold as pots/cut bunches in the produce aisle, so
    # they stay here); an explicit "fresh …" is ALWAYS produce and an explicit
    # "dried …" is always the jar. The herbs bought dried by default —
    # oregano, thyme, sage, dill, chives, tarragon, rosemary, bay — sit in
    # Herbs & Spices with their own "fresh …" keys below.
    "basil": ("leaf", FRUIT_VEG),
    "coriander": ("leaf", FRUIT_VEG),
    # US "cilantro" is always the fresh leaf, so it belongs with the produce
    # coriander and never with the "ground coriander" jar.
    "cilantro": ("leaf", FRUIT_VEG),
    "parsley": ("leaf", FRUIT_VEG),
    "mint": ("leaf", FRUIT_VEG),
    "fresh herb": ("leaf", FRUIT_VEG),
    "fresh oregano": ("leaf", FRUIT_VEG),
    "fresh thyme": ("leaf", FRUIT_VEG),
    "fresh sage": ("leaf", FRUIT_VEG),
    "fresh dill": ("leaf", FRUIT_VEG),
    "fresh chive": ("leaf", FRUIT_VEG),
    "fresh tarragon": ("leaf", FRUIT_VEG),
    "fresh rosemary": ("leaf", FRUIT_VEG),

    # --- Bakery -------------------------------------------------------
    "bread": ("bread", BAKERY),
    "white bread": ("bread", BAKERY),
    "brown bread": ("bread", BAKERY),
    "wholemeal bread": ("bread", BAKERY),
    "sourdough": ("bread", BAKERY),
    "brioche": ("bread", BAKERY),
    "teacake": ("bread", BAKERY),
    "hot cross bun": ("bread", BAKERY),
    "baguette": ("baguette", BAKERY),
    "french stick": ("baguette", BAKERY),
    "roll": ("bread", BAKERY),
    "bread roll": ("bread", BAKERY),
    "bap": ("bread", BAKERY),
    "bun": ("bread", BAKERY),
    "bagel": ("bread", BAKERY),
    "croissant": ("bread", BAKERY),
    "pain au chocolat": ("bread", BAKERY),
    "pastry": ("bread", BAKERY),
    "pitta": ("bread", BAKERY),
    "pita": ("bread", BAKERY),
    "wrap": ("bread", BAKERY),
    "tortilla": ("bread", BAKERY),
    "naan": ("bread", BAKERY),
    # The rest of the fresh flatbread shelf. Poppadoms are NOT here: they come
    # boxed and dry, and live in Cupboard with the crackers (see below).
    "chapati": ("bread", BAKERY),
    "chapatti": ("bread", BAKERY),
    "roti": ("bread", BAKERY),
    "paratha": ("bread", BAKERY),
    "flatbread": ("bread", BAKERY),
    # "garlic" (6) outranks "bread" (5) and was filing the loaf in the veg.
    "garlic bread": ("bread", BAKERY),
    "crumpet": ("bread", BAKERY),
    # Cinnamon bakery: "cinnamon" (8) beats "bun"/"roll" (below the four-char
    # substring floor, and shorter anyway), so these need explicit keys.
    "cinnamon swirl": ("bread", BAKERY),
    "cinnamon bun": ("bread", BAKERY),
    "cinnamon roll": ("bread", BAKERY),
    # "roti" (4) sits inside "rotisserie", which is a hot chicken, not a
    # flatbread; the whole word is a Meat & Fish key (below) to outrank it.
    "pancake": ("cake", BAKERY),
    "muffin": ("cake", BAKERY),
    "cupcake": ("cake", BAKERY),
    "cake": ("cake", BAKERY),
    # "cake" is only four characters, so almost any flavour word in front of it
    # outranks it: "sponge" (6, the washing-up sponge in Household), "chocolate"
    # (9, Cupboard) and "carrot" (6, Fruit & Veg) all used to carry the cake off
    # to another aisle. Plain "birthday cake" needs no key — nothing beats it.
    "sponge cake": ("cake", BAKERY),
    "victoria sponge": ("cake", BAKERY),
    "chocolate cake": ("cake", BAKERY),
    "carrot cake": ("cake", BAKERY),
    # A sausage roll is a bakery item that happens to contain a sausage;
    # "sausage" (7) beats "roll" (4) and was filing it at the meat counter.
    "sausage roll": ("sausage", BAKERY),
    "scone": ("cake", BAKERY),
    "pie": ("cake", BAKERY),
    "doughnut": ("cookie", BAKERY),
    "donut": ("cookie", BAKERY),
    "cookie": ("cookie", BAKERY),
    "biscuit": ("cookie", BAKERY),
    # Named biscuits. "digestive biscuits" already reached "biscuit"; the bare
    # brand-shaped names did not, and three of them were being pulled elsewhere
    # by a longer key: "custard" (7) and "cream" (5) to Dairy, "bread" (5) to
    # the loaves, "rich tea" to nothing at all ("tea" is below the floor).
    # NB "digestive" (9) also sits inside "digestive enzymes", which is a
    # supplement, not a biscuit; its key is on the Medicines shelf below.
    "digestive": ("cookie", BAKERY),
    "hobnob": ("cookie", BAKERY),
    "rich tea": ("cookie", BAKERY),
    "custard cream": ("cookie", BAKERY),
    "shortbread": ("cookie", BAKERY),

    # --- Meat & Fish --------------------------------------------------
    "chicken": ("meat", MEAT_FISH),
    "chicken breast": ("meat", MEAT_FISH),
    "chicken thigh": ("meat", MEAT_FISH),
    "turkey": ("meat", MEAT_FISH),
    "duck": ("meat", MEAT_FISH),
    # "roti" (Bakery) inside the hot chicken; "chicken" (7) already outranks it
    # in "rotisserie chicken", but the bare word needs its own key.
    "rotisserie": ("meat", MEAT_FISH),
    "beef": ("meat", MEAT_FISH),
    "steak": ("meat", MEAT_FISH),
    "mince": ("meat", MEAT_FISH),
    "beef mince": ("meat", MEAT_FISH),
    # US "ground beef"/"ground meat" is UK "mince"; "ground beef" already
    # resolved through "beef", the bare phrase did not.
    "ground meat": ("meat", MEAT_FISH),
    "lamb": ("meat", MEAT_FISH),
    "pork": ("meat", MEAT_FISH),
    "gammon": ("meat", MEAT_FISH),
    "bacon": ("meat", MEAT_FISH),
    "ham": ("meat", MEAT_FISH),
    "burger": ("meat", MEAT_FISH),
    "meatball": ("meat", MEAT_FISH),
    "ribs": ("meat", MEAT_FISH),
    "chop": ("meat", MEAT_FISH),
    "sausage": ("sausage", MEAT_FISH),
    "black pudding": ("sausage", MEAT_FISH),
    # The deli counter. "pepper" (6, Fruit & Veg) was filing pepperoni with the
    # bell peppers; "pepperoni pizza" then needs its own key to stay frozen.
    "salami": ("meat", MEAT_FISH),
    "pepperoni": ("meat", MEAT_FISH),
    "chorizo": ("meat", MEAT_FISH),
    "prosciutto": ("meat", MEAT_FISH),
    "fish": ("fish", MEAT_FISH),
    "salmon": ("fish", MEAT_FISH),
    "tuna": ("fish", MEAT_FISH),
    "cod": ("fish", MEAT_FISH),
    "haddock": ("fish", MEAT_FISH),
    "mackerel": ("fish", MEAT_FISH),
    "sardine": ("fish", MEAT_FISH),
    "trout": ("fish", MEAT_FISH),
    "sea bass": ("fish", MEAT_FISH),
    "fish finger": ("fish", MEAT_FISH),
    "prawn": ("fish", MEAT_FISH),
    "shrimp": ("fish", MEAT_FISH),
    "crab": ("fish", MEAT_FISH),
    "lobster": ("fish", MEAT_FISH),
    "mussel": ("fish", MEAT_FISH),
    "scallop": ("fish", MEAT_FISH),
    "squid": ("fish", MEAT_FISH),
    "calamari": ("fish", MEAT_FISH),

    # --- Dairy & Eggs -------------------------------------------------
    "milk": ("milk", DAIRY),
    "semi skimmed milk": ("milk", DAIRY),
    "skimmed milk": ("milk", DAIRY),
    "whole milk": ("milk", DAIRY),
    "oat milk": ("milk", DAIRY),
    "almond milk": ("milk", DAIRY),
    "soya milk": ("milk", DAIRY),
    "cream": ("milk", DAIRY),
    "double cream": ("milk", DAIRY),
    "single cream": ("milk", DAIRY),
    "soured cream": ("milk", DAIRY),
    "creme fraiche": ("milk", DAIRY),
    "custard": ("milk", DAIRY),
    "yoghurt": ("milk", DAIRY),
    "yogurt": ("milk", DAIRY),
    # "vanilla" (7) would otherwise outrank "yogurt" (6) on the longest-key
    # rule and file the pot with the spice jars.
    "vanilla yoghurt": ("milk", DAIRY),
    "vanilla yogurt": ("milk", DAIRY),
    "butter": ("milk", DAIRY),
    "margarine": ("milk", DAIRY),
    "spread": ("milk", DAIRY),
    "cheese": ("cheese", DAIRY),
    "cheddar": ("cheese", DAIRY),
    "mozzarella": ("cheese", DAIRY),
    "parmesan": ("cheese", DAIRY),
    "brie": ("cheese", DAIRY),
    "feta": ("cheese", DAIRY),
    "halloumi": ("cheese", DAIRY),
    "cream cheese": ("cheese", DAIRY),
    "egg": ("egg", DAIRY),
    # "egg" (3) is under the four-character substring floor, so the commonest
    # way anyone writes eggs on a list resolved to nothing at all.
    "free range egg": ("egg", DAIRY),

    # --- Cupboard -----------------------------------------------------
    "pasta": ("bowl", CUPBOARD),
    "spaghetti": ("bowl", CUPBOARD),
    "penne": ("bowl", CUPBOARD),
    "fusilli": ("bowl", CUPBOARD),
    "lasagne": ("bowl", CUPBOARD),
    "macaroni": ("bowl", CUPBOARD),
    "gnocchi": ("bowl", CUPBOARD),
    "ravioli": ("bowl", CUPBOARD),
    "tortellini": ("bowl", CUPBOARD),
    "noodle": ("bowl", CUPBOARD),
    "ramen": ("bowl", CUPBOARD),
    "rice": ("bowl", CUPBOARD),
    "basmati rice": ("bowl", CUPBOARD),
    "risotto rice": ("bowl", CUPBOARD),
    "couscous": ("bowl", CUPBOARD),
    "cous cous": ("bowl", CUPBOARD),
    "quinoa": ("bowl", CUPBOARD),
    "cereal": ("bowl", CUPBOARD),
    "porridge": ("bowl", CUPBOARD),
    "oatmeal": ("bowl", CUPBOARD),
    "oats": ("bowl", CUPBOARD),
    "cornflakes": ("bowl", CUPBOARD),
    "muesli": ("bowl", CUPBOARD),
    "granola": ("bowl", CUPBOARD),
    "soup": ("soup", CUPBOARD),
    "beans": ("soup", CUPBOARD),
    "baked beans": ("soup", CUPBOARD),
    "kidney bean": ("soup", CUPBOARD),
    "chickpea": ("soup", CUPBOARD),
    "lentil": ("soup", CUPBOARD),
    "tin": ("soup", CUPBOARD),
    "tinned tomato": ("soup", CUPBOARD),
    "chopped tomato": ("soup", CUPBOARD),
    "tomato puree": ("soup", CUPBOARD),
    # US "tomato paste" is the same tube/tin; "tomato" (6) alone was filing it
    # with the salad tomatoes.
    "tomato paste": ("soup", CUPBOARD),
    "passata": ("soup", CUPBOARD),
    "gravy": ("soup", CUPBOARD),
    # Coconut milk/cream come in a tin — the canned-goods icon in Cupboard, not
    # the "coconut" -> "apple" fruit fallback (Tabler has no coconut glyph).
    "coconut milk": ("soup", CUPBOARD),
    "coconut cream": ("soup", CUPBOARD),
    "flour": ("wheat", CUPBOARD),
    # US "cornstarch" is UK "cornflour"; "corn" (4) was filing it in the veg.
    "cornflour": ("wheat", CUPBOARD),
    "cornstarch": ("wheat", CUPBOARD),
    "yeast": ("wheat", CUPBOARD),
    "sugar": ("salt", CUPBOARD),
    "stock cube": ("salt", CUPBOARD),
    "stock pot": ("salt", CUPBOARD),
    "baking powder": ("salt", CUPBOARD),
    # Bare "soda" is deliberately NOT a key: it sits inside "soda bread"
    # (Bakery), "soda water" (Drinks) and both of these. The phrases are.
    "bicarbonate of soda": ("salt", CUPBOARD),
    "baking soda": ("salt", CUPBOARD),
    "oil": ("bottle", CUPBOARD),
    "olive oil": ("bottle", CUPBOARD),
    "vegetable oil": ("bottle", CUPBOARD),
    "sunflower oil": ("bottle", CUPBOARD),
    "vinegar": ("bottle", CUPBOARD),
    "ketchup": ("bottle", CUPBOARD),
    "mayonnaise": ("bottle", CUPBOARD),
    "mustard": ("bottle", CUPBOARD),
    "brown sauce": ("bottle", CUPBOARD),
    "soy sauce": ("bottle", CUPBOARD),
    "pasta sauce": ("bottle", CUPBOARD),
    "pesto": ("bottle", CUPBOARD),
    "curry sauce": ("bottle", CUPBOARD),
    "worcestershire sauce": ("bottle", CUPBOARD),
    "worcester sauce": ("bottle", CUPBOARD),
    "stir fry sauce": ("bottle", CUPBOARD),
    "salad dressing": ("bottle", CUPBOARD),
    "jam": ("candy", CUPBOARD),
    # "jam" (3) is under the substring floor and the berry in front of it is
    # not, so the jar was being filed with the fresh fruit.
    "strawberry jam": ("candy", CUPBOARD),
    "raspberry jam": ("candy", CUPBOARD),
    "marmalade": ("candy", CUPBOARD),
    "honey": ("candy", CUPBOARD),
    "sweets": ("candy", CUPBOARD),
    "sweet": ("candy", CUPBOARD),
    "candy": ("candy", CUPBOARD),
    "chewing gum": ("candy", CUPBOARD),
    "crisp": ("candy", CUPBOARD),
    "crisps": ("candy", CUPBOARD),
    # US "potato chips" are UK crisps — the one chips phrase that is
    # unambiguous. Bare "chips" stays in Frozen (UK oven chips) and "fries"
    # joins it there; nobody in a UK kitchen calls crisps "chips" in writing.
    # "potato" (6) was filing the packet in the veg rack.
    "potato chips": ("candy", CUPBOARD),
    "popcorn": ("candy", CUPBOARD),
    "chocolate": ("chocolate", CUPBOARD),
    "chocolate bar": ("chocolate", CUPBOARD),
    "chocolate spread": ("chocolate", CUPBOARD),
    "nutella": ("chocolate", CUPBOARD),
    "hot chocolate": ("chocolate", CUPBOARD),
    "nuts": ("acorn", CUPBOARD),
    "peanut": ("acorn", CUPBOARD),
    "almond": ("acorn", CUPBOARD),
    "peanut butter": ("acorn", CUPBOARD),
    "cracker": ("cookie", CUPBOARD),
    # Poppadoms come boxed and dry, shelved with the crisps or in world foods
    # rather than at the bakery counter, so they sit with the crackers. UK
    # supermarkets spell them every possible way, so all the common spellings
    # are keys; the plural fallback covers the trailing "s" on each.
    "poppadom": ("cookie", CUPBOARD),
    "poppadum": ("cookie", CUPBOARD),
    "poppadam": ("cookie", CUPBOARD),
    "papadom": ("cookie", CUPBOARD),
    "papadum": ("cookie", CUPBOARD),
    "papadam": ("cookie", CUPBOARD),
    "pappadam": ("cookie", CUPBOARD),
    "pappadum": ("cookie", CUPBOARD),
    "olive": ("salad", CUPBOARD),
    "gherkin": ("salad", CUPBOARD),
    "pickle": ("salad", CUPBOARD),
    # "mango" (5) was filing the jar with the fruit.
    "chutney": ("salad", CUPBOARD),
    "tea": ("cup", CUPBOARD),
    "tea bag": ("cup", CUPBOARD),
    # "herb" is a substring of "herbal"/"sherbet"; these keys are longer, so
    # the tea and the sweets stay in the cupboard.
    "herbal tea": ("cup", CUPBOARD),
    "sherbet": ("candy", CUPBOARD),
    # "ear drop" (Medicines) hides inside "pear drops"; the boiled sweets are
    # confectionery, and "chocolate"/"pear" alone are not long enough to win.
    "pear drop": ("candy", CUPBOARD),
    # "peppercorn" (10) would otherwise carry the jarred sauce to the spice
    # shelf.
    "peppercorn sauce": ("bottle", CUPBOARD),
    "coffee": ("coffee", CUPBOARD),

    # --- Herbs & Spices -----------------------------------------------
    # The seasoning shelf: dried aromatics (icon ``leaf``) and everything that
    # comes in a spice jar or grinder (icon ``salt``). Salt and flavourings
    # count as seasoning and live here; bulk baking goods and raising agents
    # (sugar, flour, baking powder, yeast) stay in Cupboard.
    "herb": ("leaf", HERBS),
    "dried herb": ("leaf", HERBS),
    "mixed herb": ("leaf", HERBS),
    "italian herb": ("leaf", HERBS),
    "oregano": ("leaf", HERBS),
    "thyme": ("leaf", HERBS),
    "sage": ("leaf", HERBS),
    "dill": ("leaf", HERBS),
    "chive": ("leaf", HERBS),
    "tarragon": ("leaf", HERBS),
    "rosemary": ("leaf", HERBS),
    "marjoram": ("leaf", HERBS),
    # "bay leaves" cannot be reached from "bay leaf" (the trailing-s fallback
    # only strips one character), so both spellings are keys.
    "bay leaf": ("leaf", HERBS),
    "bay leaves": ("leaf", HERBS),
    "dried basil": ("leaf", HERBS),
    "dried coriander": ("leaf", HERBS),
    "dried parsley": ("leaf", HERBS),
    "dried mint": ("leaf", HERBS),
    "spice": ("salt", HERBS),
    "mixed spice": ("salt", HERBS),
    "seasoning": ("salt", HERBS),
    "salt": ("salt", HERBS),
    "sea salt": ("salt", HERBS),
    "table salt": ("salt", HERBS),
    "rock salt": ("salt", HERBS),
    # Longer than the "celery" produce key, so the exact/longest rule keeps it
    # off the salad shelf.
    "celery salt": ("salt", HERBS),
    # "pepper" (bell peppers) and "chilli" stay in Fruit & Veg; the ground and
    # dried forms need their own longer keys to outrank them.
    "black pepper": ("salt", HERBS),
    "white pepper": ("salt", HERBS),
    "ground pepper": ("salt", HERBS),
    "pepper spice": ("salt", HERBS),
    "peppercorn": ("salt", HERBS),
    "chilli powder": ("salt", HERBS),
    "chilli flake": ("salt", HERBS),
    "dried chilli": ("salt", HERBS),
    "cayenne": ("salt", HERBS),
    "cayenne pepper": ("salt", HERBS),
    "paprika": ("salt", HERBS),
    "smoked paprika": ("salt", HERBS),
    "turmeric": ("salt", HERBS),
    "cinnamon": ("salt", HERBS),
    "nutmeg": ("salt", HERBS),
    "ground ginger": ("salt", HERBS),
    "cumin": ("salt", HERBS),
    "cumin seed": ("salt", HERBS),
    "ground coriander": ("salt", HERBS),
    "coriander seed": ("salt", HERBS),
    "curry powder": ("salt", HERBS),
    "garam masala": ("salt", HERBS),
    "cardamom": ("salt", HERBS),
    # Punctuation is part of the key: normalise() lowercases and collapses
    # whitespace but leaves apostrophes alone, so both spellings are listed.
    "za'atar": ("salt", HERBS),
    "zaatar": ("salt", HERBS),
    "clove": ("salt", HERBS),
    "star anise": ("salt", HERBS),
    "saffron": ("salt", HERBS),
    "allspice": ("salt", HERBS),
    "caraway": ("salt", HERBS),
    "caraway seed": ("salt", HERBS),
    "mustard seed": ("salt", HERBS),
    # Bare "fennel" is the spice jar (the bulb needs "fennel bulb", above).
    "fennel": ("salt", HERBS),
    "fennel seed": ("salt", HERBS),
    # Flavourings sit with the spices, not with the baking staples.
    "vanilla": ("salt", HERBS),
    "vanilla extract": ("salt", HERBS),
    "vanilla essence": ("salt", HERBS),
    "vanilla pod": ("salt", HERBS),

    # --- Frozen -------------------------------------------------------
    "ice cream": ("ice-cream", FROZEN),
    "ice lolly": ("ice-cream", FROZEN),
    "popsicle": ("ice-cream", FROZEN),
    "frozen peas": ("snowflake", FROZEN),
    "frozen chips": ("snowflake", FROZEN),
    "oven chips": ("snowflake", FROZEN),
    "chips": ("snowflake", FROZEN),
    # "french fries" reaches this through the substring rule.
    "fries": ("snowflake", FROZEN),
    "waffle": ("snowflake", FROZEN),
    "ice": ("snowflake", FROZEN),
    "pizza": ("pizza", FROZEN),
    "frozen pizza": ("pizza", FROZEN),
    # Not a duplicate of "pizza": "pepperoni" (9, Meat & Fish) beats "pizza" (5)
    # on the longest-key rule and drags the whole phrase to the deli counter.
    "pepperoni pizza": ("pizza", FROZEN),

    # --- Drinks -------------------------------------------------------
    "water": ("droplet", DRINKS),
    "sparkling water": ("droplet", DRINKS),
    "juice": ("bottle", DRINKS),
    "orange juice": ("bottle", DRINKS),
    "apple juice": ("bottle", DRINKS),
    "squash": ("bottle", DRINKS),
    "cordial": ("bottle", DRINKS),
    "ribena": ("bottle", DRINKS),
    "lucozade": ("bottle", DRINKS),
    # The mixer is a bottle, not the still-water droplet: "tonic" (5) ties with
    # "water" (5) inside "tonic water" and loses on position, so the phrase is
    # its own key.
    "tonic": ("bottle", DRINKS),
    "tonic water": ("bottle", DRINKS),
    "lemonade": ("bottle", DRINKS),
    "cola": ("bottle", DRINKS),
    "coke": ("bottle", DRINKS),
    "fizzy drink": ("bottle", DRINKS),
    "soft drink": ("bottle", DRINKS),
    "smoothie": ("bottle", DRINKS),
    "energy drink": ("bottle", DRINKS),
    # "vitamin" (Medicines) would otherwise carry the bottled drink to the
    # pharmacy shelf.
    "vitamin water": ("bottle", DRINKS),
    "beer": ("beer", DRINKS),
    "lager": ("beer", DRINKS),
    "ale": ("beer", DRINKS),
    "cider": ("beer", DRINKS),
    "wine": ("glass-full", DRINKS),
    "red wine": ("glass-full", DRINKS),
    "white wine": ("glass-full", DRINKS),
    "rose wine": ("glass-full", DRINKS),
    "prosecco": ("glass-champagne", DRINKS),
    "champagne": ("glass-champagne", DRINKS),
    "gin": ("glass-cocktail", DRINKS),
    "vodka": ("glass-cocktail", DRINKS),
    "whisky": ("glass-cocktail", DRINKS),
    "whiskey": ("glass-cocktail", DRINKS),
    "rum": ("glass-cocktail", DRINKS),
    # "rum" is under the four-character substring floor, so bare "spice" (5)
    # wins "spiced rum" unless the whole phrase is a key.
    "spiced rum": ("glass-cocktail", DRINKS),
    "brandy": ("glass-cocktail", DRINKS),

    # --- Household ----------------------------------------------------
    # One product, every name for it. "bog roll" needs the explicit key because
    # "roll" (4, Bakery) is inside it; the rest are spelt out so the set is
    # readable as the synonym group it is.
    "toilet roll": ("toilet-paper", HOUSEHOLD),
    "loo roll": ("toilet-paper", HOUSEHOLD),
    "loo paper": ("toilet-paper", HOUSEHOLD),
    "bog roll": ("toilet-paper", HOUSEHOLD),
    "toilet paper": ("toilet-paper", HOUSEHOLD),
    "toilet tissue": ("toilet-paper", HOUSEHOLD),
    "bathroom tissue": ("toilet-paper", HOUSEHOLD),
    "kitchen roll": ("toilet-paper", HOUSEHOLD),
    "kitchen towel": ("toilet-paper", HOUSEHOLD),
    "paper towel": ("toilet-paper", HOUSEHOLD),
    "tissue": ("toilet-paper", HOUSEHOLD),
    "kleenex": ("toilet-paper", HOUSEHOLD),
    "wipe": ("toilet-paper", HOUSEHOLD),
    "baby wipe": ("toilet-paper", HOUSEHOLD),
    "sanitary towel": ("toilet-paper", HOUSEHOLD),
    "tampon": ("toilet-paper", HOUSEHOLD),
    "cotton bud": ("toilet-paper", HOUSEHOLD),
    "cotton swab": ("toilet-paper", HOUSEHOLD),
    "q tip": ("toilet-paper", HOUSEHOLD),
    "q-tip": ("toilet-paper", HOUSEHOLD),
    "cotton wool": ("package", HOUSEHOLD),
    "bin bag": ("trash", HOUSEHOLD),
    "bin liner": ("trash", HOUSEHOLD),
    "trash bag": ("trash", HOUSEHOLD),
    "garbage bag": ("trash", HOUSEHOLD),
    "washing up liquid": ("spray", HOUSEHOLD),
    "washing-up liquid": ("spray", HOUSEHOLD),
    # "soap" (4) would otherwise send the sink stuff to the hand-soap bottle.
    "dish soap": ("spray", HOUSEHOLD),
    "dishwashing liquid": ("spray", HOUSEHOLD),
    "fairy liquid": ("spray", HOUSEHOLD),
    "dishwasher tablet": ("spray", HOUSEHOLD),
    "bleach": ("spray", HOUSEHOLD),
    # "duck" (4, Meat & Fish) was filing the loo cleaner at the butcher's.
    # Bare "toilet" is deliberately not a key: on a UK list it means loo roll
    # at least as often as it means cleaner, and guessing either way is worse
    # than the explicit phrases ("toilet paper"/"roll"/"tissue" are all keys).
    "toilet duck": ("spray", HOUSEHOLD),
    # "lime" (4) was sending the descaler to the citrus.
    "limescale": ("spray", HOUSEHOLD),
    "descaler": ("spray", HOUSEHOLD),
    "disinfectant": ("spray", HOUSEHOLD),
    "surface spray": ("spray", HOUSEHOLD),
    "cleaner": ("spray", HOUSEHOLD),
    "cleaning spray": ("spray", HOUSEHOLD),
    "air freshener": ("spray", HOUSEHOLD),
    "washing powder": ("wash-machine", HOUSEHOLD),
    "laundry detergent": ("wash-machine", HOUSEHOLD),
    "fabric softener": ("wash-machine", HOUSEHOLD),
    "soap": ("bottle", HOUSEHOLD),
    "hand soap": ("bottle", HOUSEHOLD),
    "shampoo": ("bottle", HOUSEHOLD),
    "conditioner": ("bottle", HOUSEHOLD),
    "shower gel": ("bottle", HOUSEHOLD),
    "body wash": ("bottle", HOUSEHOLD),
    "mouthwash": ("bottle", HOUSEHOLD),
    "deodorant": ("bottle", HOUSEHOLD),
    "shaving foam": ("bottle", HOUSEHOLD),
    "moisturiser": ("bottle", HOUSEHOLD),
    # "cream" (5, Dairy) again: the toiletry creams need keys longer than it.
    # The medicinal ones are on the Medicines shelf below.
    "hand cream": ("bottle", HOUSEHOLD),
    "shaving cream": ("bottle", HOUSEHOLD),
    "sponge": ("brush", HOUSEHOLD),
    "scourer": ("brush", HOUSEHOLD),
    "cloth": ("brush", HOUSEHOLD),
    # "tea" (3) is below the substring floor, so the towel never risked the
    # cupboard — but neither name resolved at all before.
    "tea towel": ("brush", HOUSEHOLD),
    "dish towel": ("brush", HOUSEHOLD),
    "toothpaste": ("dental", HOUSEHOLD),
    "toothbrush": ("dental", HOUSEHOLD),
    "razor": ("razor", HOUSEHOLD),
    "battery": ("battery", HOUSEHOLD),
    "light bulb": ("bulb", HOUSEHOLD),
    "matches": ("flame", HOUSEHOLD),
    "candle": ("candle", HOUSEHOLD),
    # "foil" alone already reaches tin/kitchen/aluminium/aluminum foil.
    "foil": ("package", HOUSEHOLD),
    "cling film": ("package", HOUSEHOLD),
    "clingfilm": ("package", HOUSEHOLD),
    # "wrap" (4) is the Bakery tortilla and was filing all of these there. The
    # non-food wraps are the easy ones to forget: they are not clingfilm
    # synonyms, they just share the word.
    "plastic wrap": ("package", HOUSEHOLD),
    "saran wrap": ("package", HOUSEHOLD),
    "wrapping paper": ("package", HOUSEHOLD),
    "gift wrap": ("package", HOUSEHOLD),
    "bubble wrap": ("package", HOUSEHOLD),
    "baking paper": ("package", HOUSEHOLD),
    "freezer bag": ("package", HOUSEHOLD),
    "sandwich bag": ("package", HOUSEHOLD),
    "nappy": ("diaper", HOUSEHOLD),
    "diaper": ("diaper", HOUSEHOLD),

    # --- Medicines ----------------------------------------------------
    # The pharmacy counter. Tablets and supplements -> ``pills``; liquids and
    # sachets -> ``medicine-syrup``; wound care and topicals -> ``first-aid-kit``.
    #
    # Half of this shelf collides with the food aisles on the longest-substring
    # rule, so several keys exist only to outrank a shorter grocery key. Bare
    # "cream", "syrup", "drop", "oil", "tablet", "gel" and "spray" are
    # deliberately NOT keys here: each of them lives inside everyday grocery
    # phrases ("double cream", "maple syrup", "olive oil", "dishwasher tablet",
    # "shower gel", "surface spray") that must stay where they are.
    "paracetamol": ("pills", MEDICINES),
    "ibuprofen": ("pills", MEDICINES),
    "nurofen": ("pills", MEDICINES),
    "anadin": ("pills", MEDICINES),
    "aspirin": ("pills", MEDICINES),
    "painkiller": ("pills", MEDICINES),
    "pain relief": ("pills", MEDICINES),
    # "pain" (4) is too generic to key on its own — it sits inside nothing here
    # but would be a standing invitation to misfile; the phrase is the key.
    "period pain": ("pills", MEDICINES),
    "medicine": ("medicine-syrup", MEDICINES),
    # "cough syrup" must be spelt out: a bare "syrup" key would drag maple and
    # golden syrup out of the cupboard.
    "cough": ("medicine-syrup", MEDICINES),
    "cough syrup": ("medicine-syrup", MEDICINES),
    "cough medicine": ("medicine-syrup", MEDICINES),
    # "sweets" (6) and "sweet" (5) are Cupboard keys inside these two.
    "cough sweet": ("pills", MEDICINES),
    "cough drop": ("pills", MEDICINES),
    "lozenge": ("pills", MEDICINES),
    "throat lozenge": ("pills", MEDICINES),
    "strepsil": ("pills", MEDICINES),
    "cold and flu": ("pills", MEDICINES),
    "cold & flu": ("pills", MEDICINES),
    "lemsip": ("medicine-syrup", MEDICINES),
    "beechams": ("medicine-syrup", MEDICINES),
    "calpol": ("medicine-syrup", MEDICINES),
    "decongestant": ("pills", MEDICINES),
    "sudafed": ("pills", MEDICINES),
    "olbas oil": ("medicine-syrup", MEDICINES),
    "antihistamine": ("pills", MEDICINES),
    "piriton": ("pills", MEDICINES),
    "hay fever": ("pills", MEDICINES),
    "hayfever": ("pills", MEDICINES),
    "indigestion": ("pills", MEDICINES),
    "heartburn": ("pills", MEDICINES),
    "antacid": ("pills", MEDICINES),
    "rennie": ("pills", MEDICINES),
    "gaviscon": ("medicine-syrup", MEDICINES),
    "laxative": ("pills", MEDICINES),
    "imodium": ("pills", MEDICINES),
    "rehydration sachet": ("medicine-syrup", MEDICINES),
    "dioralyte": ("medicine-syrup", MEDICINES),
    "vitamin": ("pills", MEDICINES),
    "multivitamin": ("pills", MEDICINES),
    # "digestive" (9, the biscuit) was shelving the supplement with the hobnobs.
    "digestive enzyme": ("pills", MEDICINES),
    # "cod" and "fish" are Meat & Fish keys; the supplements are not the fish
    # counter. ("oil" is 3 chars, below the substring floor, so it never fires.)
    "cod liver oil": ("pills", MEDICINES),
    "fish oil": ("pills", MEDICINES),
    "plaster": ("first-aid-kit", MEDICINES),
    "band aid": ("first-aid-kit", MEDICINES),
    "band-aid": ("first-aid-kit", MEDICINES),
    "bandaid": ("first-aid-kit", MEDICINES),
    "bandage": ("first-aid-kit", MEDICINES),
    "dressing pad": ("first-aid-kit", MEDICINES),
    "first aid": ("first-aid-kit", MEDICINES),
    "first aid kit": ("first-aid-kit", MEDICINES),
    # "cream" (5, Dairy) sits inside "antiseptic cream" and "suncream"; every
    # medicinal cream therefore needs a key longer than five characters.
    "antiseptic": ("first-aid-kit", MEDICINES),
    "savlon": ("first-aid-kit", MEDICINES),
    "germolene": ("first-aid-kit", MEDICINES),
    "sudocrem": ("first-aid-kit", MEDICINES),
    # What sudocrem IS, in the words people write on the list. "nappy rash" is
    # long enough to beat "cream" in "nappy rash cream" too.
    "nappy cream": ("first-aid-kit", MEDICINES),
    "nappy rash": ("first-aid-kit", MEDICINES),
    "tcp": ("first-aid-kit", MEDICINES),
    "ibuprofen gel": ("first-aid-kit", MEDICINES),
    "arnica": ("first-aid-kit", MEDICINES),
    # "spray" is the Household shelf slug and lives inside three cleaning keys;
    # the full phrase keeps the medicine out of the cupboard under the sink.
    "nasal spray": ("spray", MEDICINES),
    "insect repellent": ("spray", MEDICINES),
    "repellent": ("spray", MEDICINES),
    "sun cream": ("sun", MEDICINES),
    "suncream": ("sun", MEDICINES),
    "sunscreen": ("sun", MEDICINES),
    "sun screen": ("sun", MEDICINES),
    "sun lotion": ("sun", MEDICINES),
    "after sun": ("sun", MEDICINES),
    "aftersun": ("sun", MEDICINES),
    "eye drop": ("droplet", MEDICINES),
    "ear drop": ("droplet", MEDICINES),
    "contact lens": ("droplet", MEDICINES),
    "lens solution": ("droplet", MEDICINES),
    "hand sanitiser": ("bottle", MEDICINES),
    "hand sanitizer": ("bottle", MEDICINES),
    "sanitiser": ("bottle", MEDICINES),
    "sanitizer": ("bottle", MEDICINES),
    "thermometer": ("thermometer", MEDICINES),
    "pregnancy test": ("test-pipe", MEDICINES),
    "condom": ("package", MEDICINES),

    # --- Pet ----------------------------------------------------------
    "dog food": ("dog", PET),
    "dog": ("dog", PET),
    "dog treat": ("bone", PET),
    "cat food": ("cat", PET),
    "cat treat": ("cat", PET),
    "cat litter": ("cat", PET),
    "cat": ("cat", PET),
    "pet food": ("paw", PET),
    "rabbit food": ("paw", PET),
    "hamster food": ("paw", PET),
    "fish food": ("fish", PET),
    "bird seed": ("feather", PET),
}

ICON_VOCABULARY: list[str] = sorted({slug for slug, _category in BUILTIN.values()})


def lookup(name_norm: str) -> tuple[str, str] | None:
    """Return ``(icon_slug, category)`` for ``name_norm`` or ``None``.

    Tries, in order: an exact key match; a trailing-``s`` singular match
    (``eggs`` -> ``egg``); an ``-ies`` -> ``-y`` singular match (``nappies`` ->
    ``nappy``); then the longest built-in key that is contained in ``name_norm``
    (``cherry tomatoes`` -> ``tomato``), requiring a key of at least four
    characters to avoid spurious substring hits.

    Both folds are guarded by ``in BUILTIN``, which is what makes them safe:
    they can only ever move a name onto a key the map already holds, never
    invent one. That is why the naive ``-ies`` rewrite is not a hazard —
    "brownies" becomes "browny", finds nothing and falls through. The plain
    trailing-``s`` fold is tried FIRST so an ``-ie`` singular ("smoothies" ->
    "smoothie") is never mangled into a ``-y`` one.
    """
    if name_norm in BUILTIN:
        return BUILTIN[name_norm]
    if name_norm.endswith("s") and name_norm[:-1] in BUILTIN:
        return BUILTIN[name_norm[:-1]]
    if name_norm.endswith("ies") and name_norm[:-3] + "y" in BUILTIN:
        return BUILTIN[name_norm[:-3] + "y"]
    best = _longest_contained_key(name_norm)
    if best is None and name_norm.endswith("ies"):
        # "aa batteries" holds no key: the head noun is only spelt "battery"
        # once the plural is folded, and the fold above only fires on the whole
        # string. Retried rather than scanned first so an unfolded phrase always
        # wins, and only ever as a last resort before returning None.
        best = _longest_contained_key(name_norm[:-3] + "y")
    return BUILTIN[best] if best is not None else None


def _longest_contained_key(haystack: str) -> str | None:
    """The longest built-in key of four-plus characters inside ``haystack``."""
    best: str | None = None
    best_rank: tuple[int, int] = (0, -1)
    for key in BUILTIN:
        if len(key) < 4:
            continue
        pos = haystack.find(key)
        if pos == -1:
            continue
        # Prefer the longest key; on a tie prefer the later match, which in
        # English is usually the head noun ("cherry tomatoes" -> tomato).
        rank = (len(key), pos)
        if best is None or rank > best_rank:
            best, best_rank = key, rank
    return best
