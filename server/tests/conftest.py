import pytest
from fastapi.testclient import TestClient

from trug.app import create_app
from trug.auth import SESSION_COOKIE, hash_token
from trug.config import Settings


def make_client():
    """A TestClient authenticated as a human via a passkey SESSION COOKIE.

    The legacy per-user bearer (``tok-alice``) is retired, so the wider suite's
    "an authenticated human" is now a seeded, enrolled user with a live session
    cookie in the jar. Only the two machine bearers (ring, mcp) remain.
    """
    s = Settings.load(
        {
            "TRUG_TOKEN_RING": "tok-ring",
            "TRUG_TOKEN_MCP": "tok-mcp",
            "TRUG_DB_PATH": ":memory:",
        },
        None,
    )
    app = create_app(s)
    auth_repo = app.state.auth_repo
    auth_repo.seed_users(["alice", "bob"])
    uid = auth_repo.get_user_by_name("alice")["id"]
    # An enrolled member with a session (a credential row makes "alice" enrolled).
    auth_repo.add_credential(uid, "conftest-cred", b"pubkey", 0, None)
    auth_repo.create_session(hash_token("conftest-session"), uid, 60, "test")
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE, "conftest-session")
    return c


@pytest.fixture
def client():
    return make_client()


@pytest.fixture
def auth():
    # Auth rides the session cookie set on the client; no bearer header needed.
    return {}
