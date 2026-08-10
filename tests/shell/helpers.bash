# Shared setup for the installer / CLI tests.
#
# Everything runs against a stubbed `docker` and a throwaway HOME, so the suite
# never starts a container, never touches the developer's own ~/.trug, and runs
# in well under a second. The one thing it deliberately cannot cover is Docker
# actually working — that is what the CI end-to-end job is for.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

setup_sandbox() {
  TEST_TMP="$(mktemp -d)"
  export TEST_TMP
  export HOME="$TEST_TMP/home"
  mkdir -p "$HOME"
  export STUB_BIN="$TEST_TMP/bin"
  mkdir -p "$STUB_BIN"
  export DOCKER_LOG="$TEST_TMP/docker.log"
  : >"$DOCKER_LOG"
  export PATH="$STUB_BIN:$PATH"
  # Deterministic, colour-free output so assertions match plain strings.
  export NO_COLOR=1
  # Nothing here talks to a real container, so no test should ever sit through
  # the production health-wait. Individual tests override when that IS the point.
  export TRUG_HEALTH_TIMEOUT=3
  export TRUG_LOCAL_SRC="$REPO_ROOT"
  # On a Mac that actually has Docker Desktop installed, the daemon-down test
  # would otherwise launch it for real and then spin for a minute.
  export TRUG_DOCKER_START_TIMEOUT=1
  stub_network
  # …and `open -a Docker` must not reach the real one either.
  cat >"$STUB_BIN/open" <<'STUB'
#!/usr/bin/env bash
echo "open $*" >>"$DOCKER_LOG"
exit 0
STUB
  chmod +x "$STUB_BIN/open"
}

teardown_sandbox() {
  [ -n "${TEST_TMP:-}" ] && rm -rf "$TEST_TMP"
}

# A `docker` that records every invocation and answers the handful of queries the
# scripts make. DOCKER_STUB_MODE picks the world being simulated.
stub_docker() {
  cat >"$STUB_BIN/docker" <<'STUB'
#!/usr/bin/env bash
echo "$*" >>"$DOCKER_LOG"
case "$1" in
  info)    [ "${DOCKER_STUB_MODE:-ok}" = "daemon-down" ] && { echo "Cannot connect to the Docker daemon" >&2; exit 1; }; echo "Server Version: 29.0.0"; exit 0 ;;
  version) echo "29.0.0"; exit 0 ;;
  image)   [ "${DOCKER_STUB_IMAGE_LOCAL:-yes}" = "no" ] && { echo "No such image" >&2; exit 1; }; exit 0 ;;
  compose)
    # The subcommand is not at a fixed position: real calls look like
    # `docker compose --project-directory /x exec -T trug …`, so scan for it.
    sub=''
    for a in "$@"; do
      case "$a" in
        version|ps|exec|up|down|logs|pull) sub="$a"; break ;;
      esac
    done
    case "$sub" in
      version) echo "Docker Compose version v2.30.0"; exit 0 ;;
      ps)
        # `ps --status running -q trug` is the liveness probe; empty means down.
        for a in "$@"; do
          if [ "$a" = "--status" ]; then
            [ "${DOCKER_STUB_RUNNING:-yes}" = "no" ] && exit 0
            echo "abc123def456"; exit 0
          fi
        done
        echo "${DOCKER_STUB_PS:-trug   running}"; exit 0 ;;
      pull)    [ "${DOCKER_STUB_PULL:-ok}" = "fail" ] && { echo "manifest unknown" >&2; exit 1; }; exit 0 ;;
      exec)
        # Answer for the two commands the CLI runs inside the container.
        for a in "$@"; do
          case "$a" in
            trug-doctor) echo "OK   everything is fine"; echo "status: ok"; exit "${DOCKER_STUB_DOCTOR_EXIT:-0}" ;;
            trug.share)
              case "${DOCKER_STUB_QR:-ok}" in
                too-narrow)  exit 1 ;;
                no-module)   echo "/app/.venv/bin/python: No module named trug.share" >&2; exit 1 ;;
                not-running) echo "service \"trug\" is not running" >&2; exit 1 ;;
              esac
              echo "[QR-BLOCK]"; exit 0 ;;
          esac
        done
        exit 0 ;;
      *) exit 0 ;;
    esac ;;
esac
exit 0
STUB
  chmod +x "$STUB_BIN/docker"
}

# A fixed LAN address, so `trug share` is deterministic everywhere.
#
# Without this the tests depend on the host having a discoverable address:
# a Mac answers from `ipconfig`, a GitHub runner from `ip`, and a slim container
# with no iproute2 answers not at all — so `share` died and half a dozen tests
# failed for a reason that had nothing to do with what they were testing.
STUB_LAN_IP=192.168.1.42

stub_network() {
  cat >"$STUB_BIN/ipconfig" <<STUB
#!/usr/bin/env bash
[ "\$1" = "getifaddr" ] && { echo "$STUB_LAN_IP"; exit 0; }
exit 1
STUB
  cat >"$STUB_BIN/ip" <<STUB
#!/usr/bin/env bash
for a in "\$@"; do
  [ "\$a" = "addr" ] && { echo "2: eth0    inet $STUB_LAN_IP/24 scope global eth0"; exit 0; }
done
echo "1.1.1.1 dev eth0 src $STUB_LAN_IP"
STUB
  chmod +x "$STUB_BIN/ipconfig" "$STUB_BIN/ip"
}

# Stand in for the health probe so tests never wait on a real port.
stub_curl() {
  cat >"$STUB_BIN/curl" <<'STUB'
#!/usr/bin/env bash
echo "curl $*" >>"$DOCKER_LOG"
# `curl --version` is the installer's preflight probe for curl being usable at
# all. It must keep succeeding when CURL_STUB_EXIT is set to break the health
# probe, or the test silently exercises "curl is missing" instead of "trug never
# answered" — which is exactly what it did.
for a in "$@"; do
  [ "$a" = "--version" ] && { echo "curl 8.5.0 (stub)"; exit 0; }
done
exit "${CURL_STUB_EXIT:-0}"
STUB
  chmod +x "$STUB_BIN/curl"
}

# --- assertions --------------------------------------------------------------
#
# Use these rather than a bare `[[ "$output" == *x* ]]` on its own line. bats
# 1.14 does NOT fail a test when a `[[ ]]` compound command returns non-zero
# anywhere but the final line — 1.10 does — so a bare one is an assertion that
# is enforced on CI and ignored on a Mac. These are plain functions, so their
# failure is caught by every version, and they say what they expected.

assert_contains() { # assert_contains <needle> <haystack>
  case "$2" in
    *"$1"*) return 0 ;;
    *) printf 'expected output to contain: %s\n--- actual ---\n%s\n' "$1" "$2" >&2; return 1 ;;
  esac
}

assert_token_shape() { # assert_token_shape <value>
  case "$1" in
    ????????????????????????????????) ;;
    *) printf 'expected 32 characters, got %s: %s\n' "${#1}" "$1" >&2; return 1 ;;
  esac
  case "$1" in
    *[!A-Za-z0-9]*) printf 'expected alphanumeric only: %s\n' "$1" >&2; return 1 ;;
  esac
}

refute_contains() { # refute_contains <needle> <haystack>
  case "$2" in
    *"$1"*) printf 'expected output NOT to contain: %s\n--- actual ---\n%s\n' "$1" "$2" >&2; return 1 ;;
    *) return 0 ;;
  esac
}

# Remove a command from PATH for the duration of a test by shadowing it with a
# not-found stub (PATH manipulation alone can't hide /usr/bin).
hide_command() {
  cat >"$STUB_BIN/$1" <<'STUB'
#!/usr/bin/env bash
exit 127
STUB
  chmod +x "$STUB_BIN/$1"
}

# A ready-to-use ~/.trug, as the installer would have left it.
make_installed_home() {
  mkdir -p "$HOME/.trug/data" "$HOME/.trug/backups"
  cat >"$HOME/.trug/.env" <<'ENV'
PORT=8000
TRUG_ORIGIN=http://localhost:8000
TRUG_RP_ID=localhost
TRUG_TOKEN_RING=ringtokenringtokenringtokenring1
TRUG_TOKEN_MCP=mcptokenmcptokenmcptokenmcptoke1
TRUG_BOOTSTRAP_TOKEN=boottokenboottokenboottokenboot1
TRUG_SECRET=secretsecretsecretsecretsecret01
ENV
  cp "$REPO_ROOT/docker-compose.yml" "$HOME/.trug/docker-compose.yml" 2>/dev/null || true
}
