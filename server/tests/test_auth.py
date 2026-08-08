from fastapi.testclient import TestClient
from trug.app import create_app
from trug.auth import _authenticate_token
from trug.config import Settings


def test_user_token_maps_to_pwa_principal():
    tokens = {"alice": "ta", "bob": "tb", "ring": "tr", "mcp": "tm"}
    assert _authenticate_token(tokens, "ta") == ("alice", "pwa")
    assert _authenticate_token(tokens, "tb") == ("bob", "pwa")


def test_machine_tokens_keep_their_source():
    tokens = {"alice": "ta", "ring": "tr", "mcp": "tm"}
    assert _authenticate_token(tokens, "tr") == ("ring", "ring")
    assert _authenticate_token(tokens, "tm") == ("mcp", "mcp")


def make_client():
    s = Settings.load({"TRUG_USERS": "alice,bob",
                       "TRUG_TOKEN_ALICE": "tok-alice", "TRUG_TOKEN_BOB": "tok-bob",
                       "TRUG_TOKEN_RING": "tok-ring", "TRUG_TOKEN_MCP": "tok-mcp",
                       "TRUG_DB_PATH": ":memory:"}, None)
    return TestClient(create_app(s))


def test_missing_token_401():
    response = make_client().get("/api/list")
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_wrong_token_401():
    c = make_client()
    assert c.get("/api/list", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_non_ascii_token_401():
    c = make_client()
    # Send raw bytes so the non-ASCII token reaches the app the way a real
    # HTTP client would deliver it, rather than httpx rejecting it up front.
    header = {"Authorization": "Bearer café".encode()}
    assert c.get("/api/list", headers=header).status_code == 401


def test_healthz_unauthenticated():
    assert make_client().get("/healthz").status_code == 200
