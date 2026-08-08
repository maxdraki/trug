from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from trug.categories import DEFAULT_WALK_ORDER

# The two machine-token keys are the only env-configured bearers now. Human
# identity is a dynamic DB roster (users are created on invite and enrol a
# passkey); the very first human is claimed with a single bootstrap token. There
# are no per-user, per-household token literals — TRUG_USERS is retired and, if
# still present in the environment, is simply ignored.
_MACHINE_TOKEN_KEYS = ("ring", "mcp")
_DEFAULT_DB_PATH = "data/trug.db"
_DEFAULT_LLM_MODEL = "claude-haiku-4-5"
_DEFAULT_HOUSEHOLD = "Trug"
_DEFAULT_STATIC_DIR = "../web/dist"
_DEFAULT_RP_ID = "localhost"
_DEFAULT_ORIGIN = "http://localhost:8000"
_DEFAULT_SESSION_DAYS = 60


def _as_bool(value) -> bool:
    """Coerce an env/config value to a bool. Already-bool config values pass
    through; strings are truthy unless an explicit falsey token (false/0/no/off).

    A blank/empty value is treated as truthy so the safe default wins: a bare
    ``TRUG_RATE_LIMIT_ENABLED=`` (or compose passing an unset var as "") must NOT
    silently disable a security control. Only an explicit false/0/no/off does."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"false", "0", "no", "off"}


@dataclass
class Settings:
    tokens: dict[str, str]
    db_path: str
    walk_order: list[str]
    household: str
    llm_api_key: str | None
    llm_model: str
    llm_base_url: str | None
    static_dir: str
    rp_id: str
    origin: str
    session_days: int
    # In-process rate limiter on the sensitive auth endpoints. Default on; set
    # TRUG_RATE_LIMIT_ENABLED=false to opt out (localhost dev, or a deployment
    # already fronted by a proxy limiter). See trug/ratelimit.py.
    rate_limit_enabled: bool
    # Number of trusted reverse-proxy hops in front of the app, used to pick a
    # real client IP for the rate limiter. 0 (default) = directly exposed: ignore
    # X-Forwarded-For entirely and key on the socket peer. N >= 1 = behind N
    # trusted proxies: take the Nth-from-the-right XFF entry (the address our own
    # proxy layer inserted), never a client-chosen leftmost value. See
    # trug/ratelimit.py. Set via TRUG_TRUSTED_PROXY_HOPS.
    trusted_proxy_hops: int
    # The first-user bootstrap token: constant-time compared by the bootstrap
    # claim endpoints, never logged or returned. Pinned via TRUG_BOOTSTRAP_TOKEN,
    # else generated so a value always exists for the compare. It is only USABLE
    # while zero users exist (the claim endpoints enforce that); once the first
    # account is claimed it is inert.
    bootstrap_token: str
    generated_tokens: bool = False
    generated_keys: list[str] = field(default_factory=list)
    # True when bootstrap_token was generated (not pinned). The banner prints it
    # only when generated AND no account has been claimed yet.
    generated_bootstrap: bool = False

    @classmethod
    def load(cls, env: Mapping[str, str], config_path: Path | None) -> Settings:
        config: dict = {}
        if config_path is not None and config_path.exists():
            loaded = yaml.safe_load(config_path.read_text()) or {}
            if isinstance(loaded, dict):
                config = loaded

        def pick(env_name: str, config_key: str, default=None):
            if env_name in env:
                return env[env_name]
            if config_key in config:
                return config[config_key]
            return default

        # Only the two machine keys are env-configured bearers now (ring, mcp).
        config_tokens = config.get("tokens", {}) or {}
        tokens: dict[str, str] = {}
        for key in _MACHINE_TOKEN_KEYS:
            env_val = env.get(f"TRUG_TOKEN_{key.upper()}")
            if env_val:
                tokens[key] = env_val
            elif config_tokens.get(key):
                tokens[key] = config_tokens[key]

        # Per-key gap-fill: any machine key not pinned via env/config is generated
        # independently. Pinning some tokens never deletes the others; unpinned
        # tokens regenerate on every restart (the banner reprints them each boot).
        generated_keys: list[str] = []
        for key in _MACHINE_TOKEN_KEYS:
            if key not in tokens:
                tokens[key] = secrets.token_urlsafe(24)
                generated_keys.append(key)
        generated_tokens = bool(generated_keys)

        # First-user bootstrap token: pinned via env/config, else generated. A
        # value always exists so the claim endpoints can constant-time compare;
        # its generated-ness gates whether the banner prints it.
        bootstrap_token = pick("TRUG_BOOTSTRAP_TOKEN", "bootstrap_token", None)
        generated_bootstrap = not bootstrap_token
        if generated_bootstrap:
            bootstrap_token = secrets.token_urlsafe(24)

        walk_order = config.get("walk_order") or list(DEFAULT_WALK_ORDER)

        session_days = int(pick("TRUG_SESSION_DAYS", "session_days", _DEFAULT_SESSION_DAYS))

        rate_limit_enabled = _as_bool(
            pick("TRUG_RATE_LIMIT_ENABLED", "rate_limit_enabled", True)
        )

        trusted_proxy_hops = int(
            pick("TRUG_TRUSTED_PROXY_HOPS", "trusted_proxy_hops", 0)
        )
        if trusted_proxy_hops < 0:
            raise ValueError(
                "TRUG_TRUSTED_PROXY_HOPS must be >= 0 "
                f"(got {trusted_proxy_hops})"
            )

        return cls(
            tokens=tokens,
            db_path=pick("TRUG_DB_PATH", "db_path", _DEFAULT_DB_PATH),
            walk_order=walk_order,
            household=config.get("household", _DEFAULT_HOUSEHOLD),
            llm_api_key=pick("LLM_API_KEY", "llm_api_key", None),
            llm_model=pick("LLM_MODEL", "llm_model", _DEFAULT_LLM_MODEL),
            llm_base_url=pick("LLM_BASE_URL", "llm_base_url", None),
            static_dir=pick("TRUG_STATIC_DIR", "static_dir", _DEFAULT_STATIC_DIR),
            rp_id=pick("TRUG_RP_ID", "rp_id", _DEFAULT_RP_ID),
            origin=pick("TRUG_ORIGIN", "origin", _DEFAULT_ORIGIN),
            session_days=session_days,
            rate_limit_enabled=rate_limit_enabled,
            trusted_proxy_hops=trusted_proxy_hops,
            bootstrap_token=bootstrap_token,
            generated_tokens=generated_tokens,
            generated_keys=generated_keys,
            generated_bootstrap=generated_bootstrap,
        )
