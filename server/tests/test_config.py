from trug.config import Settings


def test_env_machine_tokens_loaded():
    s = Settings.load({"TRUG_TOKEN_RING": "c", "TRUG_TOKEN_MCP": "d"}, None)
    assert s.tokens == {"ring": "c", "mcp": "d"}
    assert not s.generated_tokens
    assert s.generated_keys == []


def test_only_machine_tokens_exist():
    # TRUG_USERS is retired: no per-user token keys are derived, even if set.
    s = Settings.load({"TRUG_USERS": "carol,dave", "TRUG_TOKEN_CAROL": "x"}, None)
    assert set(s.tokens) == {"ring", "mcp"}
    assert "carol" not in s.tokens


def test_trug_users_present_is_ignored_not_error():
    # A leftover TRUG_USERS in the environment must not raise; it's simply ignored.
    s = Settings.load({"TRUG_USERS": "alice,bob"}, None)
    assert set(s.tokens) == {"ring", "mcp"}


def test_machine_tokens_generated_when_absent():
    s = Settings.load({}, None)
    # ring + mcp -> two distinct generated tokens.
    assert s.generated_tokens and len(set(s.tokens.values())) == 2
    assert set(s.generated_keys) == {"ring", "mcp"}


def test_partial_pin_gap_fills_the_rest():
    # Pinning RING must NOT delete MCP — the missing key is gap-filled.
    s = Settings.load({"TRUG_TOKEN_RING": "c"}, None)
    assert s.tokens["ring"] == "c"
    assert "mcp" in s.tokens
    assert s.generated_tokens is True
    assert s.generated_keys == ["mcp"]


def test_bootstrap_token_generated_when_absent():
    s = Settings.load({}, None)
    assert s.bootstrap_token
    assert s.generated_bootstrap is True
    # The bootstrap token is distinct from the machine tokens.
    assert s.bootstrap_token not in s.tokens.values()


def test_bootstrap_token_pinned_from_env():
    s = Settings.load({"TRUG_BOOTSTRAP_TOKEN": "boot-xyz"}, None)
    assert s.bootstrap_token == "boot-xyz"
    assert s.generated_bootstrap is False


def test_llm_defaults():
    s = Settings.load({}, None)
    assert s.llm_api_key is None and s.llm_model == "claude-haiku-4-5"


def test_webauthn_defaults():
    s = Settings.load({}, None)
    assert s.rp_id == "localhost"
    assert s.origin == "http://localhost:8000"
    assert s.session_days == 60


def test_rate_limit_enabled_by_default():
    s = Settings.load({}, None)
    assert s.rate_limit_enabled is True


def test_rate_limit_disabled_from_env():
    for value in ("false", "0", "no", "off"):
        s = Settings.load({"TRUG_RATE_LIMIT_ENABLED": value}, None)
        assert s.rate_limit_enabled is False


def test_webauthn_from_env():
    s = Settings.load(
        {
            "TRUG_RP_ID": "trug.example.com",
            "TRUG_ORIGIN": "https://trug.example.com",
            "TRUG_SESSION_DAYS": "30",
        },
        None,
    )
    assert s.rp_id == "trug.example.com"
    assert s.origin == "https://trug.example.com"
    assert s.session_days == 30
