def normalise(name: str, known: set[str] = frozenset()) -> str:
    """Lowercase, collapse whitespace, then fold a trailing "s" against the
    catalogue keys in ``known`` — in BOTH directions, so the result does not
    depend on which spelling the household happened to add first.

    Precedence, highest first:

    1. ``key`` is already a catalogue key -> return it unchanged. An exact hit
       is never folded away; otherwise a row whose key ends in "s" (a legacy
       "lemons" row, or a genuine s-final singular) would become permanently
       unreachable by name.
    2. ``key`` ends in "s" and the singular is a catalogue key -> singularise
       ("lemons" lands on an established "lemon").
    3. ``key`` is non-empty AND ``key`` + "s" is a catalogue key -> pluralise
       ("lemon" lands on an established "lemons"). The non-empty test is not
       decoration: without it a catalogue holding a one-letter "s" row would
       turn a blank name into "s", quietly filing an empty add onto that row.
       Blank in, blank out — do not simplify the guard away (a test pins it).
    4. Otherwise return ``key`` verbatim.

    The ``in known`` guard on both folds is load-bearing and deliberately dumb:
    no stemmer, no plural list. It is what stops "asparagus" -> "asparagu",
    "couscous" -> "couscou", "sea bass" -> "sea bas". Folding only ever moves a
    name onto a key the catalogue already holds; it never invents one.

    Pure function of ``(name, known)``: no I/O, no mutation of ``known``."""

    key = " ".join(name.lower().split())
    if key in known:
        return key
    if key.endswith("s") and key[:-1] in known:
        return key[:-1]
    if key and key + "s" in known:
        return key + "s"
    return key


def tidy_name(name: str) -> str:
    """Title-case a display name conservatively: capitalise the first letter of
    each word ONLY when that word is entirely lowercase; any word already
    carrying an uppercase letter is left untouched. So "BBQ sauce" → "BBQ Sauce",
    "bbq" → "Bbq", "iPhone charger" → "iPhone Charger". Idempotent — a word this
    capitalises then contains an uppercase letter, so a second pass leaves it be.

    Purely cosmetic: applied to the stored item name and catalog display name,
    never to name_norm (dedup keys off normalise, which lowercases regardless)."""

    def fix(word: str) -> str:
        if word and word == word.lower():
            return word[0].upper() + word[1:]
        return word

    return " ".join(fix(w) for w in name.split())
