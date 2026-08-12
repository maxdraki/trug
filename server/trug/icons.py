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
    "beetroot": ("plant", FRUIT_VEG),
    "radish": ("plant", FRUIT_VEG),
    "onion": ("plant", FRUIT_VEG),
    "spring onion": ("plant", FRUIT_VEG),
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
    "aubergine": ("salad", FRUIT_VEG),
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
    "flatbread": ("bread", BAKERY),
    "crumpet": ("bread", BAKERY),
    # Cinnamon bakery: "cinnamon" (8) beats "bun"/"roll" (below the four-char
    # substring floor, and shorter anyway), so these need explicit keys.
    "cinnamon swirl": ("bread", BAKERY),
    "cinnamon bun": ("bread", BAKERY),
    "cinnamon roll": ("bread", BAKERY),
    "pancake": ("cake", BAKERY),
    "muffin": ("cake", BAKERY),
    "cupcake": ("cake", BAKERY),
    "cake": ("cake", BAKERY),
    "scone": ("cake", BAKERY),
    "pie": ("cake", BAKERY),
    "doughnut": ("cookie", BAKERY),
    "donut": ("cookie", BAKERY),
    "cookie": ("cookie", BAKERY),
    "biscuit": ("cookie", BAKERY),

    # --- Meat & Fish --------------------------------------------------
    "chicken": ("meat", MEAT_FISH),
    "chicken breast": ("meat", MEAT_FISH),
    "chicken thigh": ("meat", MEAT_FISH),
    "turkey": ("meat", MEAT_FISH),
    "duck": ("meat", MEAT_FISH),
    "beef": ("meat", MEAT_FISH),
    "steak": ("meat", MEAT_FISH),
    "mince": ("meat", MEAT_FISH),
    "beef mince": ("meat", MEAT_FISH),
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

    # --- Cupboard -----------------------------------------------------
    "pasta": ("bowl", CUPBOARD),
    "spaghetti": ("bowl", CUPBOARD),
    "penne": ("bowl", CUPBOARD),
    "fusilli": ("bowl", CUPBOARD),
    "lasagne": ("bowl", CUPBOARD),
    "macaroni": ("bowl", CUPBOARD),
    "noodle": ("bowl", CUPBOARD),
    "rice": ("bowl", CUPBOARD),
    "basmati rice": ("bowl", CUPBOARD),
    "risotto rice": ("bowl", CUPBOARD),
    "couscous": ("bowl", CUPBOARD),
    "quinoa": ("bowl", CUPBOARD),
    "cereal": ("bowl", CUPBOARD),
    "porridge": ("bowl", CUPBOARD),
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
    "passata": ("soup", CUPBOARD),
    "gravy": ("soup", CUPBOARD),
    # Coconut milk/cream come in a tin — the canned-goods icon in Cupboard, not
    # the "coconut" -> "apple" fruit fallback (Tabler has no coconut glyph).
    "coconut milk": ("soup", CUPBOARD),
    "coconut cream": ("soup", CUPBOARD),
    "flour": ("wheat", CUPBOARD),
    "yeast": ("wheat", CUPBOARD),
    "sugar": ("salt", CUPBOARD),
    "stock cube": ("salt", CUPBOARD),
    "baking powder": ("salt", CUPBOARD),
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
    "curry sauce": ("bottle", CUPBOARD),
    "stir fry sauce": ("bottle", CUPBOARD),
    "salad dressing": ("bottle", CUPBOARD),
    "jam": ("candy", CUPBOARD),
    "marmalade": ("candy", CUPBOARD),
    "honey": ("candy", CUPBOARD),
    "sweets": ("candy", CUPBOARD),
    "sweet": ("candy", CUPBOARD),
    "chewing gum": ("candy", CUPBOARD),
    "crisp": ("candy", CUPBOARD),
    "crisps": ("candy", CUPBOARD),
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
    "olive": ("salad", CUPBOARD),
    "gherkin": ("salad", CUPBOARD),
    "pickle": ("salad", CUPBOARD),
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
    "frozen peas": ("snowflake", FROZEN),
    "frozen chips": ("snowflake", FROZEN),
    "oven chips": ("snowflake", FROZEN),
    "chips": ("snowflake", FROZEN),
    "waffle": ("snowflake", FROZEN),
    "ice": ("snowflake", FROZEN),
    "pizza": ("pizza", FROZEN),
    "frozen pizza": ("pizza", FROZEN),

    # --- Drinks -------------------------------------------------------
    "water": ("droplet", DRINKS),
    "sparkling water": ("droplet", DRINKS),
    "juice": ("bottle", DRINKS),
    "orange juice": ("bottle", DRINKS),
    "apple juice": ("bottle", DRINKS),
    "squash": ("bottle", DRINKS),
    "cordial": ("bottle", DRINKS),
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
    "toilet roll": ("toilet-paper", HOUSEHOLD),
    "loo roll": ("toilet-paper", HOUSEHOLD),
    "toilet paper": ("toilet-paper", HOUSEHOLD),
    "kitchen roll": ("toilet-paper", HOUSEHOLD),
    "tissue": ("toilet-paper", HOUSEHOLD),
    "kleenex": ("toilet-paper", HOUSEHOLD),
    "wipe": ("toilet-paper", HOUSEHOLD),
    "baby wipe": ("toilet-paper", HOUSEHOLD),
    "sanitary towel": ("toilet-paper", HOUSEHOLD),
    "tampon": ("toilet-paper", HOUSEHOLD),
    "cotton bud": ("toilet-paper", HOUSEHOLD),
    "cotton wool": ("package", HOUSEHOLD),
    "bin bag": ("trash", HOUSEHOLD),
    "bin liner": ("trash", HOUSEHOLD),
    "washing up liquid": ("spray", HOUSEHOLD),
    "fairy liquid": ("spray", HOUSEHOLD),
    "dishwasher tablet": ("spray", HOUSEHOLD),
    "bleach": ("spray", HOUSEHOLD),
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
    "sponge": ("brush", HOUSEHOLD),
    "scourer": ("brush", HOUSEHOLD),
    "cloth": ("brush", HOUSEHOLD),
    "toothpaste": ("dental", HOUSEHOLD),
    "toothbrush": ("dental", HOUSEHOLD),
    "razor": ("razor", HOUSEHOLD),
    "battery": ("battery", HOUSEHOLD),
    "light bulb": ("bulb", HOUSEHOLD),
    "matches": ("flame", HOUSEHOLD),
    "candle": ("candle", HOUSEHOLD),
    "foil": ("package", HOUSEHOLD),
    "cling film": ("package", HOUSEHOLD),
    "baking paper": ("package", HOUSEHOLD),
    "freezer bag": ("package", HOUSEHOLD),
    "sandwich bag": ("package", HOUSEHOLD),
    "nappy": ("diaper", HOUSEHOLD),

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
    # "cod" and "fish" are Meat & Fish keys; the supplements are not the fish
    # counter. ("oil" is 3 chars, below the substring floor, so it never fires.)
    "cod liver oil": ("pills", MEDICINES),
    "fish oil": ("pills", MEDICINES),
    "plaster": ("first-aid-kit", MEDICINES),
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
    (``eggs`` -> ``egg``); then the longest built-in key that is contained
    in ``name_norm`` (``cherry tomatoes`` -> ``tomato``), requiring a key of
    at least four characters to avoid spurious substring hits.
    """
    if name_norm in BUILTIN:
        return BUILTIN[name_norm]
    if name_norm.endswith("s") and name_norm[:-1] in BUILTIN:
        return BUILTIN[name_norm[:-1]]
    best: str | None = None
    best_rank: tuple[int, int] = (0, -1)
    for key in BUILTIN:
        if len(key) < 4:
            continue
        pos = name_norm.find(key)
        if pos == -1:
            continue
        # Prefer the longest key; on a tie prefer the later match, which in
        # English is usually the head noun ("cherry tomatoes" -> tomato).
        rank = (len(key), pos)
        if best is None or rank > best_rank:
            best, best_rank = key, rank
    return BUILTIN[best] if best is not None else None
