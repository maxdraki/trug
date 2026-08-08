def normalise(name: str, known: set[str] = frozenset()) -> str:
    key = " ".join(name.lower().split())
    if key.endswith("s") and key[:-1] in known:
        return key[:-1]
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
