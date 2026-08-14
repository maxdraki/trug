from types import SimpleNamespace


def test_typeahead_ranks_prefix_first(client, auth):
    for name in ["milk", "mints", "almond milk"]:
        client.post("/api/items", json={"name": name}, headers=auth)
    names = [e["name_norm"] for e in client.get("/api/catalog?q=mi", headers=auth).json()]
    assert names[0] in ("milk", "mints")          # prefix matches before substring
    assert "almond milk" in names


def test_top_respects_n(client, auth):
    for name in ["a", "b", "c"]:
        client.post("/api/items", json={"name": name}, headers=auth)
    assert len(client.get("/api/catalog/top?n=2", headers=auth).json()) == 2


def test_catalog_requires_auth(client):
    client.cookies.clear()  # drop the fixture's session cookie to test anonymous
    assert client.get("/api/catalog?q=mi").status_code == 401
    assert client.get("/api/catalog/top").status_code == 401


def forget(client, auth, name_norm):
    """The forget call as a client makes it: the key is a QUERY parameter, so
    httpx percent-encodes it and the server sees it verbatim."""
    return client.delete("/api/catalog", params={"name_norm": name_norm}, headers=auth)


def test_forget_drops_the_shortcut_from_the_tray(client, auth):
    """A bad transcription ("Marty Rice" for basmati rice) is offered forever
    otherwise: it is in the catalogue, so `catalog_top` keeps proposing it, and
    until now the only way to remove one was to delete an ITEM of that name."""
    client.post("/api/items", json={"name": "Marty Rice"}, headers=auth)
    assert forget(client, auth, "marty rice").status_code == 204

    top = client.get("/api/catalog/top", headers=auth).json()
    assert "marty rice" not in [e["name_norm"] for e in top]
    assert client.get("/api/catalog?q=marty", headers=auth).json() == []


def test_forget_leaves_an_item_of_that_name_on_the_list(client, auth):
    """A shortcut and a thing in the trolley are different objects. Someone
    tidying "Papa Dums" out of the tray while poppadoms are genuinely on this
    week's list must not lose the row they are about to shop for."""
    client.post("/api/items", json={"name": "Papa Dums"}, headers=auth)
    forget(client, auth, "papa dums")

    active = client.get("/api/list", headers=auth).json()["active"]
    assert "Papa Dums" in [i["name"] for group in active.values() for i in group]


def test_forgetting_twice_reports_the_second_as_gone(client, auth):
    """404 rather than a cheerful 204, so a client that queued the same forget
    twice (or raced another device) can tell the row was already gone."""
    client.post("/api/items", json={"name": "Co O"}, headers=auth)
    assert forget(client, auth, "co o").status_code == 204
    assert forget(client, auth, "co o").status_code == 404


def test_forget_is_broadcast_so_other_devices_stop_offering_it(client, auth):
    """The tray is a fetch, not a subscription, so a phone left open on the
    kitchen counter would keep offering a shortcut that no longer exists until
    someone reloaded it. The frame is what tells it to refetch."""
    client.post("/api/items", json={"name": "Co O"}, headers=auth)
    published = []
    client.app.state.bus = SimpleNamespace(publish=lambda n, d: published.append((n, d)))

    forget(client, auth, "co o")
    assert published == [("catalog_forgotten", {"name_norm": "co o"})]

    # Nothing to forget, nothing to announce.
    published.clear()
    forget(client, auth, "co o")
    assert published == []


def test_forget_takes_only_the_key_it_was_given(client, auth):
    """The match is on the WHOLE key, not a prefix of it. A route that matched
    ``LIKE key || '%'`` — or that folded the key through ``normalise`` — would
    take "Milkshake" and "Milk Chocolate" away with "Milk": silent data loss on
    a shared list, discovered next Saturday when the shortcut is missing and
    nobody knows why."""
    for name in ["Milk", "Milkshake", "Milk Chocolate"]:
        client.post("/api/items", json={"name": name}, headers=auth)

    assert forget(client, auth, "milk").status_code == 204

    surviving = {e["name_norm"] for e in client.get("/api/catalog/top?n=50", headers=auth).json()}
    assert surviving >= {"milkshake", "milk chocolate"}
    assert "milk" not in surviving
    # …and the neighbours are still forgettable in their own right, i.e. they
    # are real rows rather than leftovers of a half-done delete.
    assert forget(client, auth, "milkshake").status_code == 204


def test_forget_does_not_match_a_differently_cased_key(client, auth):
    """Catalogue keys are already normalised, so a key arriving in another case
    is not this row spelled differently — it is a client that has invented one.
    Lower-casing here would look harmless and would fold "Milk" onto "milk",
    which is one small step from folding a key onto a NEIGHBOURING row; 404 says
    what actually happened."""
    client.post("/api/items", json={"name": "Marty Rice"}, headers=auth)

    assert forget(client, auth, "Marty Rice").status_code == 404
    assert forget(client, auth, "MARTY RICE").status_code == 404
    # The row is untouched, so the verbatim key still works.
    assert forget(client, auth, "marty rice").status_code == 204


def test_forget_unknown_entry_is_404(client, auth):
    assert forget(client, auth, "never heard of it").status_code == 404


def test_forget_requires_auth(client):
    client.cookies.clear()  # drop the fixture's session cookie to test anonymous
    assert client.delete("/api/catalog", params={"name_norm": "milk"}).status_code == 401


def test_forget_a_shortcut_whose_key_contains_a_slash(client, auth):
    """The whole reason the key is a query parameter and not a path segment.
    `normalise` keeps "/", so "Salt / Pepper" is catalogued as "salt / pepper";
    in a path that key is unreachable however it is encoded, because Starlette
    percent-decodes before routing and the segment splits — the pill would come
    back "Couldn't forget" forever."""
    client.post("/api/items", json={"name": "Salt / Pepper"}, headers=auth)
    assert forget(client, auth, "salt / pepper").status_code == 204
    assert client.get("/api/catalog?q=salt", headers=auth).json() == []


def test_forget_handles_the_punctuation_a_capture_can_produce(client, auth):
    """Every character a shopper (or a transcription) can put in a name has to
    survive the round trip: percent and plus (which a query decoder treats
    specially), ampersand and hash (which delimit), apostrophes, question marks
    and non-ASCII."""
    names = [
        "100% Juice",           # percent — a bare % is an invalid escape
        "Salt & Vinegar",       # ampersand — parameter separator
        "Za'atar",              # apostrophe
        "7 + 7 Bars",           # plus — decodes to a space if left raw
        "Item #4",              # hash — a fragment delimiter
        "What? Sauce",          # question mark — starts the query itself
        "Crème Fraîche",        # non-ASCII, needs UTF-8 percent-encoding
        "Rice/Pasta",           # slash with no surrounding spaces
    ]
    for name in names:
        client.post("/api/items", json={"name": name}, headers=auth)

    catalogued = {e["name_norm"] for e in client.get("/api/catalog/top?n=50", headers=auth).json()}
    for name in names:
        key = name.lower()
        assert key in catalogued, f"{name} was not catalogued as {key!r}"
        assert forget(client, auth, key).status_code == 204
        assert forget(client, auth, key).status_code == 404, f"{key!r} was not matched verbatim"


def test_forget_without_a_key_is_a_422_not_a_silent_no_op(client, auth):
    """A client that drops the parameter must be told, not answered 404 as if
    the shortcut had already gone."""
    assert client.delete("/api/catalog", headers=auth).status_code == 422
