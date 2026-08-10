"""Tests for the `trug-doctor` diagnostic + recovery CLI.

Covers every origin/RP-ID validation case (including IP rejection and the
http-on-a-non-localhost secure-context failure), the observed-host mismatch
(and that localhost/healthcheck do NOT trip it), the single-enrolled-member
lockout warning firing at exactly one member, the exit-code roll-up, and the
two recovery escape hatches proven end to end: `recover --invite` mints a token
the REAL `/auth/register/*` ceremony accepts (via the SoftwareAuthenticator),
and `recover --reset-bootstrap` reopens a working claim that then closes again.
"""

from __future__ import annotations

import io

from fastapi.testclient import TestClient

from trug import doctor
from trug.app import create_app
from trug.auth import SESSION_COOKIE, hash_token
from trug.auth_repo import AuthRepository
from trug.config import Settings
from trug.llm_config import LLMConfigStore

from tests.webauthn_authenticator import SoftwareAuthenticator

RP_ID = "localhost"
ORIGIN = "http://localhost:8000"
BOOTSTRAP_TOKEN = "boot-secret-doctor"


# --- helpers ----------------------------------------------------------------


def _settings(**over):
    env = {
        "TRUG_TOKEN_RING": "tok-ring",
        "TRUG_TOKEN_MCP": "tok-mcp",
        "TRUG_BOOTSTRAP_TOKEN": BOOTSTRAP_TOKEN,
        "TRUG_DB_PATH": ":memory:",
        "TRUG_RP_ID": RP_ID,
        "TRUG_ORIGIN": ORIGIN,
    }
    env.update(over)
    return Settings.load(env, None)


def _ids(checks):
    return {c.id: c for c in checks}


def _e2e_client():
    """A pristine app wired for the software authenticator (mirrors the e2e
    ceremony tests)."""
    settings = _settings()
    app = create_app(settings)
    return TestClient(app), app, settings


def _origin_headers(extra=None):
    h = {"origin": ORIGIN}
    if extra:
        h.update(extra)
    return h


def _boot_headers():
    return _origin_headers({"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"})


def _claim_first_user(c, name="alice"):
    r = c.post("/auth/bootstrap/claim/options", json={"name": name}, headers=_boot_headers())
    assert r.status_code == 200, r.text
    device = SoftwareAuthenticator(origin=ORIGIN)
    cred = device.make_credential(r.json())
    r = c.post(
        "/auth/bootstrap/claim/verify",
        json={"name": name, "credential": cred},
        headers=_boot_headers(),
    )
    assert r.status_code == 200, r.text
    return device, r


# --- origin / RP-ID validation ----------------------------------------------


def test_origin_all_ok_for_localhost_http():
    checks = _ids(doctor._origin_checks(_settings()))
    assert checks["origin.valid"].status == doctor.OK
    assert checks["rp_id.bare_host"].status == doctor.OK
    assert checks["rp_id.not_ip"].status == doctor.OK
    assert checks["rp_id.matches_origin"].status == doctor.OK
    # localhost over http is a secure context.
    assert checks["origin.secure_context"].status == doctor.OK


def test_origin_https_domain_ok():
    checks = _ids(
        doctor._origin_checks(
            _settings(TRUG_ORIGIN="https://trug.example.com", TRUG_RP_ID="trug.example.com")
        )
    )
    assert checks["origin.valid"].status == doctor.OK
    assert checks["origin.secure_context"].status == doctor.OK
    assert checks["rp_id.matches_origin"].status == doctor.OK


def test_origin_invalid_url_fails():
    checks = _ids(doctor._origin_checks(_settings(TRUG_ORIGIN="not-a-url")))
    assert checks["origin.valid"].status == doctor.FAIL


def test_rp_id_with_scheme_port_path_fails_and_suggests_bare():
    checks = _ids(doctor._origin_checks(_settings(TRUG_RP_ID="https://trug.example.com:8000/app")))
    c = checks["rp_id.bare_host"]
    assert c.status == doctor.FAIL
    assert c.remedy["env"]["TRUG_RP_ID"] == "trug.example.com"
    assert set(c.detail["issues"]) == {"scheme", "port", "path"}


def test_rp_id_ipv4_rejected():
    checks = _ids(
        doctor._origin_checks(_settings(TRUG_RP_ID="192.168.1.10", TRUG_ORIGIN="https://192.168.1.10"))
    )
    assert checks["rp_id.not_ip"].status == doctor.FAIL


def test_rp_id_ipv6_rejected():
    checks = _ids(doctor._origin_checks(_settings(TRUG_RP_ID="::1", TRUG_ORIGIN="https://example.com")))
    assert checks["rp_id.not_ip"].status == doctor.FAIL


def test_rp_id_mismatch_origin_host_fails():
    checks = _ids(
        doctor._origin_checks(
            _settings(TRUG_ORIGIN="https://trug.example.com", TRUG_RP_ID="other.example.org")
        )
    )
    c = checks["rp_id.matches_origin"]
    assert c.status == doctor.FAIL
    assert c.remedy["env"]["TRUG_RP_ID"] == "trug.example.com"


def test_rp_id_registrable_parent_matches():
    # RP ID is a registrable parent of the origin host — allowed.
    checks = _ids(
        doctor._origin_checks(
            _settings(TRUG_ORIGIN="https://app.trug.example.com", TRUG_RP_ID="trug.example.com")
        )
    )
    assert checks["rp_id.matches_origin"].status == doctor.OK


def test_origin_host_ip_loopback_says_use_localhost():
    # `trug` served on http://127.0.0.1:8000 IS a secure context, so the browser
    # starts the ceremony and then dies on the RP ID. The remedy is one word.
    checks = _ids(doctor._origin_checks(_settings(TRUG_ORIGIN="http://127.0.0.1:8000")))
    c = checks["origin.host_is_ip"]
    assert c.status == doctor.FAIL
    assert "localhost" in c.message
    assert c.remedy["env"] == {
        "TRUG_ORIGIN": "http://localhost:8000",
        "TRUG_RP_ID": "localhost",
    }


def test_origin_host_ip_loopback_ipv6_says_use_localhost():
    checks = _ids(doctor._origin_checks(_settings(TRUG_ORIGIN="http://[::1]:8000")))
    c = checks["origin.host_is_ip"]
    assert c.status == doctor.FAIL
    assert c.remedy["env"]["TRUG_ORIGIN"] == "http://localhost:8000"


def test_origin_host_ip_debian_self_hostname_is_still_loopback():
    # Debian and Raspberry Pi OS map the box's own hostname to 127.0.1.1, so this
    # is what a copy-paste from the machine's own resolution gives you. It is
    # loopback and a secure context; treating it as a LAN address sent people off
    # to install Tailscale to fix a typo.
    checks = _ids(doctor._origin_checks(_settings(TRUG_ORIGIN="http://127.0.1.1:8000")))
    c = checks["origin.host_is_ip"]
    assert c.status == doctor.FAIL
    assert c.remedy["env"]["TRUG_ORIGIN"] == "http://localhost:8000"
    # …and it must not also be accused of being an insecure context.
    assert checks["origin.secure_context"].status == doctor.OK


def test_origin_host_ip_lan_has_no_rename_remedy():
    # A LAN address can't be renamed into working — there is no env edit that
    # fixes it, so offering one would be a lie. Point at the real answers.
    checks = _ids(
        doctor._origin_checks(
            _settings(TRUG_ORIGIN="http://192.168.1.5:8000", TRUG_RP_ID="192.168.1.5")
        )
    )
    c = checks["origin.host_is_ip"]
    assert c.status == doctor.FAIL
    assert c.remedy is None
    assert "trug share" in c.message


def test_origin_host_ip_absent_when_origin_is_a_name():
    checks = _ids(doctor._origin_checks(_settings(TRUG_ORIGIN="https://trug.example.com",
                                                 TRUG_RP_ID="trug.example.com")))
    assert checks["origin.host_is_ip"].status == doctor.OK


def test_rp_id_mismatch_never_recommends_an_ip_as_rp_id():
    # The old remedy read TRUG_RP_ID=127.0.0.1 straight off the origin host —
    # advice that the very next check (rp_id.not_ip) fails you for taking.
    # With an IP origin the match check isn't meaningful, so it stands down and
    # origin.host_is_ip carries the guidance instead.
    checks = _ids(doctor._origin_checks(_settings(TRUG_ORIGIN="http://127.0.0.1:8000")))
    assert "rp_id.matches_origin" not in checks
    for c in checks.values():
        rp = (c.remedy or {}).get("env", {}).get("TRUG_RP_ID")
        assert rp is None or not doctor._is_ip(rp), f"{c.id} recommends an IP as the RP ID"


def test_http_non_localhost_secure_context_fails():
    checks = _ids(
        doctor._origin_checks(
            _settings(TRUG_ORIGIN="http://trug.example.com", TRUG_RP_ID="trug.example.com")
        )
    )
    c = checks["origin.secure_context"]
    assert c.status == doctor.FAIL
    # Names the three remedies.
    assert "Tailscale" in c.message and "Cloudflare" in c.message and "reverse proxy" in c.message


# --- observed host vs configured origin -------------------------------------


def test_observed_host_mismatch_warns_with_exact_remedy():
    settings = _settings(TRUG_ORIGIN="https://trug.example.com", TRUG_RP_ID="trug.example.com")
    app = create_app(settings)
    c = TestClient(app)
    # A request reaching the server as a DIFFERENT external host.
    c.get("/healthz", headers={"host": "trug.tail1234.ts.net"})
    observed = app.state.auth_repo.observed_hosts()
    # /healthz must NOT be recorded even with an external host.
    assert observed == []

    # A non-healthz request on the wrong host IS recorded.
    c.get("/api/list", headers={"host": "trug.tail1234.ts.net"})
    check = doctor._observed_host_check(settings, app.state.auth_repo.observed_hosts())
    assert check.status == doctor.WARN
    assert check.remedy["env"] == {
        "TRUG_ORIGIN": "https://trug.tail1234.ts.net",
        "TRUG_RP_ID": "trug.tail1234.ts.net",
    }


def test_observed_lan_ip_is_the_share_path_not_a_misconfiguration():
    # Everyone on a trial install browses http://<lan-ip>:8000 via `trug share`,
    # so the middleware records it. Warning about that would make `trug status`
    # exit 1 on every healthy trial install — and the remedy it used to hand over
    # was TRUG_RP_ID=<that IP>, which WebAuthn forbids and rp_id.not_ip fails you
    # for. Advice that makes things worse is worse than no advice.
    settings = _settings(TRUG_ORIGIN="http://localhost:8000", TRUG_RP_ID="localhost")
    observed = [{"host": "192.168.4.134", "hit_count": 8}]
    c = doctor._observed_host_check(settings, observed)
    assert c.status == doctor.OK
    assert c.remedy is None
    assert "trug share" in c.message


def test_configured_name_never_reached_is_not_healthy():
    # Tailscale Serve stops, or the name stops resolving, so every request now
    # arrives by IP. Treating that as the share path would report a green
    # instance on which nobody can create an account and the configured name is
    # simply dead. The share path is distinguishable: there, the configured host
    # is being reached too.
    settings = _settings(
        TRUG_ORIGIN="https://trug.tail1234.ts.net", TRUG_RP_ID="trug.tail1234.ts.net"
    )
    c = doctor._observed_host_check(settings, [{"host": "192.168.1.42", "hit_count": 12}])
    assert c.status == doctor.WARN
    assert "never been reached" in c.message
    # No remedy env: there is no setting that fixes a name that doesn't resolve.
    assert c.remedy is None


def test_share_path_is_ok_once_the_configured_host_is_also_seen():
    settings = _settings(
        TRUG_ORIGIN="https://trug.tail1234.ts.net", TRUG_RP_ID="trug.tail1234.ts.net"
    )
    observed = [
        {"host": "192.168.1.42", "hit_count": 12},
        {"host": "trug.tail1234.ts.net", "hit_count": 3},
    ]
    assert doctor._observed_host_check(settings, observed).status == doctor.OK


def test_observed_name_mismatch_still_warns_with_a_usable_remedy():
    settings = _settings(TRUG_ORIGIN="http://localhost:8000", TRUG_RP_ID="localhost")
    observed = [{"host": "trug.tail1234.ts.net", "hit_count": 3}]
    c = doctor._observed_host_check(settings, observed)
    assert c.status == doctor.WARN
    assert c.remedy["env"]["TRUG_RP_ID"] == "trug.tail1234.ts.net"


def test_observed_host_mismatch_never_recommends_an_ip():
    # Belt and braces across every shape: a mixed list must not fall back to
    # suggesting the IP either.
    settings = _settings(TRUG_ORIGIN="http://localhost:8000", TRUG_RP_ID="localhost")
    for observed in (
        [{"host": "192.168.1.5", "hit_count": 2}],
        [{"host": "10.0.0.9", "hit_count": 1}, {"host": "192.168.1.5", "hit_count": 4}],
        [{"host": "::1", "hit_count": 1}],
    ):
        c = doctor._observed_host_check(settings, observed)
        rp = (c.remedy or {}).get("env", {}).get("TRUG_RP_ID")
        assert rp is None or not doctor._is_ip(rp)


def test_observed_host_localhost_not_recorded():
    settings = _settings(TRUG_ORIGIN="https://trug.example.com", TRUG_RP_ID="trug.example.com")
    app = create_app(settings)
    c = TestClient(app)
    c.get("/api/list", headers={"host": "localhost"})
    c.get("/api/list", headers={"host": "127.0.0.1"})
    assert app.state.auth_repo.observed_hosts() == []
    # And with nothing observed the check is OK (does not cry wolf).
    check = doctor._observed_host_check(settings, app.state.auth_repo.observed_hosts())
    assert check.status == doctor.OK


def test_observed_host_matching_configured_is_ok():
    settings = _settings(TRUG_ORIGIN="https://trug.example.com", TRUG_RP_ID="trug.example.com")
    app = create_app(settings)
    c = TestClient(app)
    c.get("/api/list", headers={"host": "trug.example.com"})
    check = doctor._observed_host_check(settings, app.state.auth_repo.observed_hosts())
    assert check.status == doctor.OK


def test_observed_host_x_forwarded_host_wins():
    settings = _settings(TRUG_ORIGIN="https://trug.example.com", TRUG_RP_ID="trug.example.com")
    app = create_app(settings)
    c = TestClient(app)
    # Internal Host is trug:8000, but the tunnel carries the real host.
    c.get("/api/list", headers={"host": "trug:8000", "x-forwarded-host": "public.ts.net"})
    hosts = [o["host"] for o in app.state.auth_repo.observed_hosts()]
    assert hosts == ["public.ts.net"]


# --- roster / single point of failure ---------------------------------------


def _enrol(auth_repo, name, creds=1):
    auth_repo.seed_users([name])
    uid = auth_repo.get_user_by_name(name)["id"]
    for i in range(creds):
        auth_repo.add_credential(uid, f"{name}-cred-{i}", b"pubkey", 0, None)
    return uid


def test_single_enrolled_member_warns():
    auth_repo = AuthRepository(":memory:")
    _enrol(auth_repo, "alice", creds=1)
    checks = _ids(doctor._roster_checks(auth_repo))
    assert "roster.single_point_of_failure" in checks
    assert checks["roster.single_point_of_failure"].status == doctor.WARN
    assert checks["roster.single_point_of_failure"].detail["member"] == "alice"


def test_single_enrolled_member_with_two_creds_no_warn():
    auth_repo = AuthRepository(":memory:")
    _enrol(auth_repo, "alice", creds=2)
    checks = _ids(doctor._roster_checks(auth_repo))
    assert "roster.single_point_of_failure" not in checks


def test_two_enrolled_members_no_warn():
    auth_repo = AuthRepository(":memory:")
    _enrol(auth_repo, "alice", creds=1)
    _enrol(auth_repo, "bob", creds=1)
    checks = _ids(doctor._roster_checks(auth_repo))
    # The warning fires at exactly one enrolled member, not two.
    assert "roster.single_point_of_failure" not in checks
    assert checks["roster.summary"].detail["enrolled"] == 2


def test_roster_reports_outstanding_invites_and_claimable():
    auth_repo = AuthRepository(":memory:")
    checks = _ids(doctor._roster_checks(auth_repo))
    # Fresh instance: claimable, no invites.
    assert checks["roster.claimable"].detail["claimable"] is True
    assert checks["roster.outstanding_invites"].detail["outstanding_invites"] == 0


# --- tokens -----------------------------------------------------------------


def test_pinned_tokens_shown_by_default_redacted_on_flag():
    auth_repo = AuthRepository(":memory:")
    # PINNED machine tokens: the value is the same across processes, so the
    # doctor shows it (and redacts on demand).
    settings = _settings()
    shown = _ids(doctor._token_checks(settings, auth_repo, redact=False))
    assert shown["token.ring"].status == doctor.OK
    assert shown["token.ring"].detail["value"] == settings.tokens["ring"]
    redacted = _ids(doctor._token_checks(settings, auth_repo, redact=True))
    assert redacted["token.ring"].detail["value"] == doctor._REDACTED


def test_generated_token_value_not_shown_and_no_remedy_value():
    # A GENERATED (unpinned) token rotates per process, so the value this
    # separate process would print is a dead token — never show it, and never
    # emit a remedy.env value that would write the wrong token into .env.
    auth_repo = AuthRepository(":memory:")
    settings = Settings.load({"TRUG_DB_PATH": ":memory:"}, None)
    checks = _ids(doctor._token_checks(settings, auth_repo, redact=False))
    ring = checks["token.ring"]
    assert ring.status == doctor.WARN
    assert "value" not in ring.detail
    # No remedy.env carrying a (dead) value; the stabilising action is to PIN.
    assert ring.remedy is None or "value" not in str(ring.remedy.get("env", {}))
    # And the freshly-minted value never appears anywhere in the JSON output.
    import io as _io
    import json as _json

    buf = _io.StringIO()
    doctor.render_json(doctor.diagnose(settings, auth_repo, LLMConfigStore(":memory:")), buf)
    assert settings.tokens["ring"] not in buf.getvalue()


def test_generated_bootstrap_value_not_shown():
    # Unclaimed instance with a generated bootstrap token: same cross-process
    # hazard — no value shown, no remedy.env value.
    auth_repo = AuthRepository(":memory:")
    settings = Settings.load({"TRUG_DB_PATH": ":memory:"}, None)
    checks = _ids(doctor._token_checks(settings, auth_repo, redact=False))
    boot = checks["token.bootstrap"]
    assert boot.status == doctor.WARN
    assert "value" not in boot.detail
    assert boot.remedy is None or "value" not in str(boot.remedy.get("env", {}))
    assert settings.bootstrap_token not in "".join(
        f"{c.message} {c.detail} {c.remedy}" for c in checks.values()
    )


def test_bootstrap_token_not_shown_once_claimed():
    auth_repo = AuthRepository(":memory:")
    _enrol(auth_repo, "alice")
    checks = _ids(doctor._token_checks(_settings(), auth_repo, redact=False))
    assert checks["token.bootstrap"].status == doctor.OK
    assert checks["token.bootstrap"].detail["claimed"] is True
    assert "value" not in checks["token.bootstrap"].detail


# --- enrichment -------------------------------------------------------------


def test_enrichment_source_env_when_llm_key_set():
    settings = _settings(LLM_API_KEY="sk-test")
    store = LLMConfigStore(":memory:")
    checks = _ids(doctor._enrichment_checks(settings, store))
    assert checks["enrichment.llm_config"].detail["source"] == "env"


def test_enrichment_unreadable_key_warns():
    # A row encrypted under a secret that is now gone -> unreadable, warns.
    store_encrypted = LLMConfigStore(":memory:", secret="original")
    store_encrypted.save("anthropic", "sk-live", "claude-haiku-4-5", None)
    # Re-open the SAME in-memory connection object is not possible; instead
    # simulate a rotated secret by pointing a new store with no secret at a row
    # written encrypted. Use a temp file so both stores see the row.
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = f"{d}/t.db"
        enc = LLMConfigStore(path, secret="original")
        enc.save("anthropic", "sk-live", "claude-haiku-4-5", None)
        gone = LLMConfigStore(path, secret=None)
        checks = _ids(doctor._enrichment_checks(_settings(), gone))
        assert checks["enrichment.key_decrypt"].status == doctor.WARN


# --- Fix 1.2: rate-limiter proxy-trust check --------------------------------


def test_proxy_trust_fails_when_behind_proxy_but_hops_zero():
    auth_repo = AuthRepository(":memory:")
    auth_repo.mark_forwarded_header_seen()
    settings = _settings()  # rate_limit_enabled default on, hops default 0
    checks = _ids(doctor._ratelimit_checks(settings, auth_repo))
    c = checks["ratelimit.proxy_trust"]
    assert c.status == doctor.FAIL
    assert c.remedy["env"]["TRUG_TRUSTED_PROXY_HOPS"] == "1"


def test_proxy_trust_ok_when_no_forwarded_header_seen():
    auth_repo = AuthRepository(":memory:")
    settings = _settings()  # hops 0 but no proxy header observed = direct deploy
    checks = _ids(doctor._ratelimit_checks(settings, auth_repo))
    assert checks["ratelimit.proxy_trust"].status == doctor.OK


def test_proxy_trust_ok_when_hops_configured():
    auth_repo = AuthRepository(":memory:")
    auth_repo.mark_forwarded_header_seen()
    settings = _settings(TRUG_TRUSTED_PROXY_HOPS="1")
    checks = _ids(doctor._ratelimit_checks(settings, auth_repo))
    assert checks["ratelimit.proxy_trust"].status == doctor.OK


# --- M1: disabled limiter + a seen header must not claim "none seen" ----------


def test_proxy_trust_ok_message_consistent_when_disabled_and_header_seen():
    auth_repo = AuthRepository(":memory:")
    auth_repo.mark_forwarded_header_seen()
    settings = _settings(TRUG_RATE_LIMIT_ENABLED="false")  # hops 0, header seen
    c = _ids(doctor._ratelimit_checks(settings, auth_repo))["ratelimit.proxy_trust"]
    assert c.status == doctor.OK
    assert c.detail["forwarded_header_seen"] is True
    # The old message hard-coded "no proxy forwarded header seen" even here.
    assert "no proxy forwarded header seen" not in c.message


# --- C1: the doctor proxy_trust corroborates the signal by socket peer --------


def _c1_settings():
    return Settings.load(
        {
            "TRUG_TOKEN_RING": "tok-ring",
            "TRUG_TOKEN_MCP": "tok-mcp",
            "TRUG_BOOTSTRAP_TOKEN": BOOTSTRAP_TOKEN,
            "TRUG_DB_PATH": ":memory:",
            "TRUG_RP_ID": "trug.example.com",
            "TRUG_ORIGIN": "https://trug.example.com",
        },
        None,
    )


def test_proxy_trust_stays_ok_when_only_public_peer_xff_seen():
    # A public attacker forging XFF on a direct (hops==0) deployment must not
    # push the doctor to FAIL and hand out TRUG_TRUSTED_PROXY_HOPS=1.
    app = create_app(_c1_settings())
    # 8.8.8.8 is a genuinely global peer (documentation ranges are is_private).
    TestClient(app, client=("8.8.8.8", 40000)).get(
        "/api/list", headers={"x-forwarded-for": "1.2.3.4"}
    )
    checks = _ids(doctor._ratelimit_checks(app.state.settings, app.state.auth_repo))
    assert checks["ratelimit.proxy_trust"].status == doctor.OK


def test_proxy_trust_fails_when_private_peer_xff_seen_with_hops_zero():
    # A genuine co-located proxy (loopback peer) forwarding a header, while hops
    # is still 0, is the real misconfiguration the check must FAIL on.
    app = create_app(_c1_settings())
    TestClient(app, client=("127.0.0.1", 40000)).get(
        "/api/list", headers={"x-forwarded-for": "1.2.3.4"}
    )
    checks = _ids(doctor._ratelimit_checks(app.state.settings, app.state.auth_repo))
    c = checks["ratelimit.proxy_trust"]
    assert c.status == doctor.FAIL
    assert c.remedy["env"]["TRUG_TRUSTED_PROXY_HOPS"] == "1"


# --- I2: the read-only doctor must survive a pre-migration DB -----------------


def test_forwarded_header_seen_false_on_pre_migration_readonly_db(tmp_path):
    import sqlite3

    db = tmp_path / "t.db"
    AuthRepository(str(db))  # full schema + migration + WAL
    LLMConfigStore(str(db))
    # Simulate a DB created by an older schema: drop the column the migration
    # would have added. The read-only doctor path never migrates, so the reader
    # must tolerate its absence rather than crash the whole diagnosis.
    conn = sqlite3.connect(str(db))
    conn.execute("ALTER TABLE meta DROP COLUMN forwarded_header_seen_at")
    conn.commit()
    conn.close()

    ro = AuthRepository(str(db), read_only=True)
    assert ro.forwarded_header_seen() is False
    # And the full diagnose runs end to end without raising.
    llm_store = LLMConfigStore(str(db))
    checks = doctor.diagnose(_settings(TRUG_DB_PATH=str(db)), ro, llm_store)
    assert any(c.id == "ratelimit.proxy_trust" for c in checks)


# --- exit-code roll-up ------------------------------------------------------


def test_summarise_exit_codes():
    ok = [doctor.Check("a", doctor.OK, "")]
    warn = [doctor.Check("a", doctor.OK, ""), doctor.Check("b", doctor.WARN, "")]
    fail = [doctor.Check("b", doctor.WARN, ""), doctor.Check("c", doctor.FAIL, "")]
    assert doctor.summarise(ok) == ("ok", 0)
    assert doctor.summarise(warn) == ("warn", 1)
    assert doctor.summarise(fail) == ("error", 2)


def test_json_render_shape():
    checks = [
        doctor.Check(
            "origin.observed_host_mismatch",
            doctor.WARN,
            "msg",
            {"configured": "a", "observed": ["b"]},
            {"env": {"TRUG_ORIGIN": "https://b", "TRUG_RP_ID": "b"}},
        )
    ]
    buf = io.StringIO()
    doctor.render_json(checks, buf)
    import json

    payload = json.loads(buf.getvalue())
    assert payload["status"] == "warn"
    assert payload["exit_code"] == 1
    entry = payload["checks"][0]
    assert entry["id"] == "origin.observed_host_mismatch"
    assert entry["remedy"]["env"]["TRUG_RP_ID"] == "b"


# --- recover --invite: proven through the REAL register ceremony -------------


def test_recover_invite_token_accepted_by_real_register_flow():
    c, app, settings = _e2e_client()
    _claim_first_user(c, "alice")  # an enrolled household exists

    # Mint an invite for a locked-out / new member straight from the CLI.
    buf = io.StringIO()
    result = doctor.recover_invite(settings, app.state.auth_repo, "bob", buf)
    assert result["ok"]
    token = result["token"]
    assert f"#invite={token}" in buf.getvalue()

    # The REAL registration ceremony (no session) accepts that token and lets
    # bob back in — the escape hatch actually works.
    fresh = TestClient(app)  # no session cookie
    r = fresh.post("/auth/register/options", json={"invite": token}, headers=_origin_headers())
    assert r.status_code == 200, r.text
    bob_device = SoftwareAuthenticator(origin=ORIGIN)
    cred = bob_device.make_credential(r.json())
    r = fresh.post(
        "/auth/register/verify",
        json={"invite": token, "credential": cred},
        headers=_origin_headers(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["user"] == "bob"
    assert fresh.cookies.get(SESSION_COOKIE)  # a working session


def test_recover_invite_creates_pending_user_when_absent():
    c, app, settings = _e2e_client()
    _claim_first_user(c, "alice")
    buf = io.StringIO()
    doctor.recover_invite(settings, app.state.auth_repo, "newperson", buf)
    assert app.state.auth_repo.get_user_by_name("newperson") is not None


# --- recover --reset-bootstrap ----------------------------------------------


def test_reset_bootstrap_refuses_with_enrolled_credential():
    c, app, settings = _e2e_client()
    _claim_first_user(c, "alice")  # one enrolled credential
    buf = io.StringIO()
    result = doctor.recover_reset_bootstrap(settings, app.state.auth_repo, buf, force=False)
    assert result["ok"] is False
    assert result["reason"] == "enrolled_exist"
    assert app.state.auth_repo.bootstrap_reopen_active() is False


def test_reset_bootstrap_force_requires_confirmation():
    c, app, settings = _e2e_client()
    _claim_first_user(c, "alice")
    buf = io.StringIO()
    # Wrong confirmation input aborts and changes nothing.
    result = doctor.recover_reset_bootstrap(
        settings, app.state.auth_repo, buf, force=True, input_func=lambda _p: "nope"
    )
    assert result["ok"] is False
    assert result["reason"] == "not_confirmed"
    assert app.state.auth_repo.bootstrap_reopen_active() is False


def test_reset_bootstrap_force_reopens_claim_then_closes_again():
    c, app, settings = _e2e_client()
    _claim_first_user(c, "alice")  # enrolled

    buf = io.StringIO()
    result = doctor.recover_reset_bootstrap(
        settings, app.state.auth_repo, buf, force=True, yes=True
    )
    assert result["ok"] and result["reopened"]
    assert app.state.auth_repo.bootstrap_reopen_active() is True

    # The reopened claim yields a WORKING session through the real ceremony,
    # re-enrolling a device onto the existing 'alice'.
    fresh = TestClient(app)
    r = fresh.post("/auth/bootstrap/claim/options", json={"name": "alice"}, headers=_boot_headers())
    assert r.status_code == 200, r.text
    device = SoftwareAuthenticator(origin=ORIGIN)
    cred = device.make_credential(r.json())
    r = fresh.post(
        "/auth/bootstrap/claim/verify",
        json={"name": "alice", "credential": cred},
        headers=_boot_headers(),
    )
    assert r.status_code == 200, r.text
    assert fresh.cookies.get(SESSION_COOKIE)
    # That session actually authenticates the API.
    assert fresh.get("/api/list").status_code == 200

    # ...and bootstrap has closed again (one-time property restored).
    assert app.state.auth_repo.bootstrap_reopen_active() is False
    r = TestClient(app).post(
        "/auth/bootstrap/claim/options", json={"name": "eve"}, headers=_boot_headers()
    )
    assert r.status_code == 403


def test_reset_bootstrap_succeeds_without_force_when_zero_enrolled():
    # A full lockout: a user exists but no enrolled credential. No --force needed.
    auth_repo = AuthRepository(":memory:")
    auth_repo.create_user("alice")  # pending, zero credentials
    buf = io.StringIO()
    result = doctor.recover_reset_bootstrap(_settings(), auth_repo, buf, force=False)
    assert result["ok"] and result["reopened"]
    assert auth_repo.bootstrap_reopen_active() is True


# --- recover --revoke-sessions ----------------------------------------------


def test_revoke_sessions_invalidates_all():
    auth_repo = AuthRepository(":memory:")
    uid = _enrol(auth_repo, "alice")
    auth_repo.create_session(hash_token("s1"), uid, 60, "a")
    auth_repo.create_session(hash_token("s2"), uid, 60, "b")
    assert auth_repo.active_session_count() == 2
    buf = io.StringIO()
    result = doctor.recover_revoke_sessions(auth_repo, buf)
    assert result["revoked"] == 2
    assert auth_repo.active_session_count() == 0


# --- diagnose end to end ----------------------------------------------------


def test_diagnose_healthy_localhost_has_no_fail():
    # A pinned-token localhost instance should have no FAILs (warnings allowed).
    auth_repo = AuthRepository(":memory:")
    store = LLMConfigStore(":memory:")
    checks = doctor.diagnose(_settings(), auth_repo, store)
    assert all(c.status != doctor.FAIL for c in checks)


# --- C2: journal_mode lives in the repo, no sqlite3 in doctor/app -----------


def test_journal_mode_reports_wal_on_file_repo(tmp_path):
    repo = AuthRepository(str(tmp_path / "t.db"))
    assert repo.journal_mode() == "wal"


def test_storage_journal_mode_ok_on_file_repo(tmp_path):
    db = tmp_path / "t.db"
    settings = _settings(TRUG_DB_PATH=str(db))
    repo = AuthRepository(str(db))
    store = LLMConfigStore(str(db))
    checks = _ids(doctor.diagnose(settings, repo, store))
    assert checks["storage.journal_mode"].status == doctor.OK
    assert checks["storage.journal_mode"].detail["journal_mode"].lower() == "wal"


def test_no_sqlite3_symbol_in_doctor_or_app():
    # CLAUDE.md hard rule: no SQLite-isms outside the repository layer.
    import inspect

    import trug.app
    import trug.doctor

    assert "sqlite3" not in inspect.getsource(trug.doctor)
    assert "sqlite3" not in inspect.getsource(trug.app)


# --- I1: diagnose is read-only (never creates or migrates the DB) -----------


def _diagnose_env(tmp_path, db):
    return {
        "TRUG_DB_PATH": str(db),
        "TRUG_RP_ID": "localhost",
        "TRUG_ORIGIN": "http://localhost:8000",
        "TRUG_BOOTSTRAP_TOKEN": "boot",
        "TRUG_TOKEN_RING": "r",
        "TRUG_TOKEN_MCP": "m",
    }


def test_diagnose_does_not_create_missing_db(tmp_path, monkeypatch, capsys):
    db = tmp_path / "nope.db"
    for k, v in _diagnose_env(tmp_path, db).items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)  # no config.yaml here
    code = doctor.main([])
    # The doctor must NOT leave a (possibly root-owned) DB behind.
    assert not db.exists()
    assert code >= 1
    out = capsys.readouterr().out
    assert "not initialized" in out.lower()


def test_diagnose_missing_db_json_reports_uninitialized(tmp_path, monkeypatch, capsys):
    db = tmp_path / "nope.db"
    for k, v in _diagnose_env(tmp_path, db).items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)
    code = doctor.main(["--json"])
    assert not db.exists()
    assert code >= 1
    import json as _json

    payload = _json.loads(capsys.readouterr().out)
    assert payload["exit_code"] >= 1


def test_diagnose_does_not_mutate_existing_db(tmp_path, monkeypatch):
    import os

    db = tmp_path / "t.db"
    # Keep the RW connections alive for the whole test: if these were discarded,
    # Python could GC (and close) the WAL connection mid-test, and closing a WAL
    # handle checkpoints — rewriting the main DB file AFTER `before` is captured
    # and making this assertion flaky. What we are proving is that the read-only
    # *diagnosis* does not mutate; a mid-test checkpoint of our own writer must
    # not masquerade as that.
    _repo = AuthRepository(str(db))  # create + migrate + WAL
    _store = LLMConfigStore(str(db))
    before = os.stat(str(db)).st_mtime_ns
    for k, v in _diagnose_env(tmp_path, db).items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)
    doctor.main([])
    assert _repo is not None and _store is not None  # keep references live
    after = os.stat(str(db)).st_mtime_ns
    # A read-only diagnosis leaves the main DB file untouched.
    assert before == after


def test_readonly_repo_reads_without_migrating(tmp_path):
    db = tmp_path / "t.db"
    AuthRepository(str(db))
    ro = AuthRepository(str(db), read_only=True)
    assert ro.journal_mode() == "wal"
    assert ro.user_count() == 0


# --- I4: live bootstrap-reopen must not read as OK; cancel path -------------


def test_reopen_with_users_present_warns():
    auth_repo = AuthRepository(":memory:")
    _enrol(auth_repo, "alice", creds=1)
    auth_repo.reopen_bootstrap()
    checks = _ids(doctor._roster_checks(auth_repo))
    c = checks["roster.claimable"]
    assert c.status in (doctor.WARN, doctor.FAIL)
    assert "cancel-reset" in c.message


def test_recover_cancel_reset_clears_flag_and_refuses_claim(tmp_path):
    c, app, settings = _e2e_client()
    _claim_first_user(c, "alice")
    app.state.auth_repo.reopen_bootstrap()
    assert app.state.auth_repo.bootstrap_reopen_active() is True

    buf = io.StringIO()
    result = doctor.recover_cancel_reset(app.state.auth_repo, buf)
    assert result["ok"] and result["cancelled"] is True
    assert app.state.auth_repo.bootstrap_reopen_active() is False

    # With the window closed, a fresh bootstrap claim is refused again.
    r = TestClient(app).post(
        "/auth/bootstrap/claim/options", json={"name": "eve"}, headers=_boot_headers()
    )
    assert r.status_code == 403


def test_recover_cancel_reset_when_nothing_active():
    auth_repo = AuthRepository(":memory:")
    _enrol(auth_repo, "alice")
    buf = io.StringIO()
    result = doctor.recover_cancel_reset(auth_repo, buf)
    assert result["ok"] and result["cancelled"] is False


# --- Minor: a single-label / bare-TLD RP_ID is a broken config --------------


def test_bare_tld_rp_id_warns():
    checks = _ids(
        doctor._origin_checks(_settings(TRUG_RP_ID="com", TRUG_ORIGIN="https://com"))
    )
    assert checks["rp_id.registrable"].status == doctor.WARN


def test_localhost_rp_id_no_dot_does_not_warn():
    checks = _ids(doctor._origin_checks(_settings()))
    # localhost legitimately has no dot — it must not trip the guard.
    assert "rp_id.registrable" not in checks or checks["rp_id.registrable"].status == doctor.OK
