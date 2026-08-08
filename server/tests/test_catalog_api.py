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
