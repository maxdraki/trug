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

# Stand in for the health probe so tests never wait on a real port.
stub_curl() {
  cat >"$STUB_BIN/curl" <<'STUB'
#!/usr/bin/env bash
echo "curl $*" >>"$DOCKER_LOG"
exit "${CURL_STUB_EXIT:-0}"
STUB
  chmod +x "$STUB_BIN/curl"
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
