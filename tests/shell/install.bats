#!/usr/bin/env bats
#
# install.sh. The tests are mostly about the ways an installer can quietly do
# damage — clobbering a live token, regenerating a secret that decrypts stored
# data, writing an empty value that beats config.yaml — rather than about the
# happy path, which is the easy part.

load helpers

setup() {
  setup_sandbox
  stub_docker
  stub_curl
  INSTALL="$REPO_ROOT/install.sh"
}

teardown() { teardown_sandbox; }

# Load the script's functions without running the installer.
lib() { TRUG_LIB_ONLY=1 . "$INSTALL"; }

@test "gen_token: 32 alphanumeric characters, nothing a shell or Compose will eat" {
  lib
  for _ in 1 2 3 4 5; do
    tok="$(gen_token)"
    [ "${#tok}" -eq 32 ]
    [[ "$tok" =~ ^[A-Za-z0-9]{32}$ ]]
  done
}

@test "gen_token: a fresh value every time" {
  lib
  [ "$(gen_token)" != "$(gen_token)" ]
}

@test "write_env: every key gets a non-empty value" {
  lib
  write_env "$TEST_TMP/.env"
  for key in TRUG_TOKEN_RING TRUG_TOKEN_MCP TRUG_BOOTSTRAP_TOKEN TRUG_SECRET; do
    run grep -E "^${key}=.+" "$TEST_TMP/.env"
    [ "$status" -eq 0 ]
  done
  # An empty env var BEATS config.yaml in trug's config precedence, so a blank
  # value here is worse than no line at all.
  run grep -E '^[A-Z_]+=$' "$TEST_TMP/.env"
  [ "$status" -ne 0 ]
}

@test "write_env: never regenerates a token that already exists" {
  lib
  write_env "$TEST_TMP/.env"
  before="$(grep '^TRUG_TOKEN_MCP=' "$TEST_TMP/.env")"
  secret_before="$(grep '^TRUG_SECRET=' "$TEST_TMP/.env")"
  write_env "$TEST_TMP/.env"
  [ "$(grep '^TRUG_TOKEN_MCP=' "$TEST_TMP/.env")" = "$before" ]
  # TRUG_SECRET encrypts the stored LLM key: rotating it on a re-run would make
  # an already-working instance quietly lose that key.
  [ "$(grep '^TRUG_SECRET=' "$TEST_TMP/.env")" = "$secret_before" ]
}

@test "write_env: leaves an operator's hand-edited origin alone" {
  lib
  write_env "$TEST_TMP/.env"
  sed -i.bak 's|^TRUG_ORIGIN=.*|TRUG_ORIGIN=https://trug.example.com|' "$TEST_TMP/.env"
  write_env "$TEST_TMP/.env"
  run grep '^TRUG_ORIGIN=https://trug.example.com$' "$TEST_TMP/.env"
  [ "$status" -eq 0 ]
}

@test "write_compose: pulls the image and never builds from source" {
  lib
  write_compose "$TEST_TMP/docker-compose.yml"
  run grep -E '^\s*build:' "$TEST_TMP/docker-compose.yml"
  [ "$status" -ne 0 ]
  run grep 'image: ghcr.io/maxdraki/trug' "$TEST_TMP/docker-compose.yml"
  [ "$status" -eq 0 ]
}

@test "write_compose: TRUG_IMAGE picks the image, so CI can test the build in front of it" {
  # Without this the end-to-end job installs whatever :latest currently is —
  # which pulls successfully at any time — and passes regardless of the commit.
  TRUG_IMAGE=trug:ci lib
  write_compose "$TEST_TMP/docker-compose.yml"
  run grep '^    image: trug:ci$' "$TEST_TMP/docker-compose.yml"
  [ "$status" -eq 0 ]
}

@test "write_compose: leaves PORT for Compose to expand, not the installer" {
  lib
  write_compose "$TEST_TMP/docker-compose.yml"
  run grep -F '"${PORT:-8000}:${PORT:-8000}"' "$TEST_TMP/docker-compose.yml"
  [ "$status" -eq 0 ]
}

@test "write_compose: binds every interface, because the point is the other phone" {
  lib
  write_compose "$TEST_TMP/docker-compose.yml"
  run grep -E '127\.0\.0\.1:' "$TEST_TMP/docker-compose.yml"
  [ "$status" -ne 0 ]
}

@test "write_compose: uses the short env_file form so old Compose still parses it" {
  lib
  write_compose "$TEST_TMP/docker-compose.yml"
  run grep -E 'required: false' "$TEST_TMP/docker-compose.yml"
  [ "$status" -ne 0 ]
}

@test "runs under dash, which is what /bin/sh is on a Pi" {
  # Every other test runs this under bash, which is not the shell it ships to.
  # shellcheck -s sh catches bashism syntax but not runtime divergence.
  command -v dash >/dev/null 2>&1 || skip "dash not installed"
  run dash "$INSTALL"
  [ "$status" -eq 0 ]
  [ -f "$HOME/.trug/.env" ]
  [ -x "$HOME/.local/bin/trug" ]
}

@test "a full install lays out ~/.trug and starts the container" {
  run bash "$INSTALL"
  [ "$status" -eq 0 ]
  [ -f "$HOME/.trug/.env" ]
  [ -f "$HOME/.trug/docker-compose.yml" ]
  [ -d "$HOME/.trug/data" ]
  [ -x "$HOME/.local/bin/trug" ]
  # Always scoped to ~/.trug, never to whatever directory the user happened to
  # be standing in when they ran the installer.
  grep -q -- "--project-directory $HOME/.trug pull" "$DOCKER_LOG"
  grep -q -- "--project-directory $HOME/.trug up -d" "$DOCKER_LOG"
}

@test "a full install prints the share link, not a token to grep out of the logs" {
  run bash "$INSTALL"
  [ "$status" -eq 0 ]
  [[ "$output" == *"trug share"* ]]
  [[ "$output" != *"docker compose logs"* ]]
}

@test "re-running is a repair, not a reinstall: data and tokens survive" {
  run bash "$INSTALL"
  [ "$status" -eq 0 ]
  echo "pretend database" >"$HOME/.trug/data/trug.db"
  token="$(grep '^TRUG_TOKEN_MCP=' "$HOME/.trug/.env")"

  run bash "$INSTALL"
  [ "$status" -eq 0 ]
  [ "$(cat "$HOME/.trug/data/trug.db")" = "pretend database" ]
  [ "$(grep '^TRUG_TOKEN_MCP=' "$HOME/.trug/.env")" = "$token" ]
}

@test "pull fails but the image is already here: starts what it has" {
  export DOCKER_STUB_PULL=fail
  export DOCKER_STUB_IMAGE_LOCAL=yes
  run bash "$INSTALL"
  [ "$status" -eq 0 ]
  [[ "$output" == *"already on this machine"* ]]
  grep -q -- "--project-directory $HOME/.trug up -d" "$DOCKER_LOG"
}

@test "pull fails with no local image: refuses rather than starting nothing" {
  export DOCKER_STUB_PULL=fail
  export DOCKER_STUB_IMAGE_LOCAL=no
  run bash "$INSTALL"
  [ "$status" -ne 0 ]
  [[ "$output" == *"Couldn't pull the image"* ]]
  run grep -q -- "--project-directory $HOME/.trug up -d" "$DOCKER_LOG"
  [ "$status" -ne 0 ]
}

@test "no docker: names the ways to get one and does not pretend to install it" {
  hide_command docker
  run bash "$INSTALL"
  [ "$status" -ne 0 ]
  [[ "$output" == *"Docker Desktop"* ]]
  [[ "$output" == *"OrbStack"* ]]
  [[ "$output" == *"Colima"* ]]
  [ ! -d "$HOME/.trug" ]
}

@test "docker installed but not running: says so instead of failing at the pull" {
  export DOCKER_STUB_MODE=daemon-down
  run bash "$INSTALL"
  [ "$status" -ne 0 ]
  [[ "$output" == *"not running"* ]] || [[ "$output" == *"isn't running"* ]]
}

@test "32-bit Raspberry Pi OS: names the cause before the pull fails on it" {
  cat >"$STUB_BIN/uname" <<'STUB'
#!/usr/bin/env bash
[ "$1" = "-m" ] && { echo armv7l; exit 0; }
exec /usr/bin/uname "$@"
STUB
  chmod +x "$STUB_BIN/uname"
  run bash "$INSTALL"
  [ "$status" -ne 0 ]
  [[ "$output" == *"64-bit"* ]]
}

@test "never blocks on stdin, because curl | sh has none to read" {
  run grep -nE '(^|[^_[:alnum:]])read[[:space:]]+(-[a-z]+[[:space:]]+)*[A-Za-z_]' "$INSTALL"
  [ "$status" -ne 0 ]
}

@test "a child that reads stdin cannot eat the rest of the piped script" {
  # Under `curl … | sh` this script's stdin IS the pipe carrying its own
  # remaining bytes. A docker that reads stdin used to swallow them, killing the
  # shell on a syntax error after the container had already been started.
  cat >"$STUB_BIN/docker" <<'STUB'
#!/usr/bin/env bash
cat >/dev/null   # the hostile bit: drain stdin
echo "$*" >>"$DOCKER_LOG"
case "$1" in
  info)    echo "Server Version: 29.0.0"; exit 0 ;;
  version) echo "29.0.0"; exit 0 ;;
  image)   exit 0 ;;
esac
exit 0
STUB
  chmod +x "$STUB_BIN/docker"
  run bash -c "cat '$INSTALL' | bash"
  [ "$status" -eq 0 ]
  [[ "$output" != *"syntax error"* ]]
  [[ "$output" != *"unexpected"* ]]
  # The assertions that matter. An earlier attempt at this fixed the stdin
  # problem with `exec 0</dev/null`, which discards the very pipe the shell is
  # reading the script from: bash then exits 0 having done nothing at all, and
  # dash dies on an unterminated string. Checking only for a clean exit and no
  # syntax error passes for a total silent no-op.
  [ -f "$HOME/.trug/.env" ]
  [ -x "$HOME/.local/bin/trug" ]
  [[ "$output" == *"trug is up"* ]]
}

@test "the piped install works under every shell someone might have as /bin/sh" {
  # `curl … | sh` is the documented command, and which shell that is depends on
  # the machine. Each one buffers the script differently, so a bug in how fd 0
  # is handled can be invisible in one and fatal in another.
  for shell in bash dash sh zsh; do
    command -v "$shell" >/dev/null 2>&1 || continue
    rm -rf "$HOME/.trug" "$HOME/.local"
    run bash -c "cat '$INSTALL' | $shell"
    [ "$status" -eq 0 ] || { echo "$shell exited $status: $output"; return 1; }
    [ -f "$HOME/.trug/.env" ] || { echo "$shell installed nothing"; return 1; }
  done
}

@test "a hand-set key the installer knows nothing about survives a re-run" {
  # The repair path must not un-fix a proxied deployment. An installer that
  # rebuilt .env from a template silently reset TRUG_TRUSTED_PROXY_HOPS to 0,
  # collapsing every client onto one rate-limit bucket — with the doctor still
  # reporting healthy, because what it can see is valid.
  run bash "$INSTALL"
  [ "$status" -eq 0 ]
  {
    echo "TRUG_TRUSTED_PROXY_HOPS=1"
    echo "LLM_API_KEY=sk-not-a-real-key"
    echo "# why: we sit behind Caddy"
  } >>"$HOME/.trug/.env"

  run bash "$INSTALL"
  [ "$status" -eq 0 ]
  grep -q '^TRUG_TRUSTED_PROXY_HOPS=1$' "$HOME/.trug/.env"
  grep -q '^LLM_API_KEY=sk-not-a-real-key$' "$HOME/.trug/.env"
  grep -q '^# why: we sit behind Caddy$' "$HOME/.trug/.env"
}

@test "TRUG_PORT moves the port on a re-run, because that is the advice we print" {
  # When the health wait times out, the installer says the port is probably
  # taken and to re-run with TRUG_PORT=8080. That has to actually work.
  run bash "$INSTALL"
  [ "$status" -eq 0 ]
  grep -q '^PORT=8000$' "$HOME/.trug/.env"

  TRUG_PORT=8080 run bash "$INSTALL"
  [ "$status" -eq 0 ]
  grep -q '^PORT=8080$' "$HOME/.trug/.env"
  # The origin follows the port while it is still the default shape…
  grep -q '^TRUG_ORIGIN=http://localhost:8080$' "$HOME/.trug/.env"
}

@test "TRUG_PORT never rewrites an origin someone set by hand" {
  run bash "$INSTALL"
  [ "$status" -eq 0 ]
  awk '/^TRUG_ORIGIN=/ { print "TRUG_ORIGIN=https://trug.example.com"; next } { print }' \
    "$HOME/.trug/.env" >"$TEST_TMP/e" && cp "$TEST_TMP/e" "$HOME/.trug/.env"

  TRUG_PORT=8080 run bash "$INSTALL"
  [ "$status" -eq 0 ]
  grep -q '^PORT=8080$' "$HOME/.trug/.env"
  grep -q '^TRUG_ORIGIN=https://trug.example.com$' "$HOME/.trug/.env"
}

@test "a re-run with no TRUG_PORT leaves the port exactly where it was" {
  TRUG_PORT=8080 run bash "$INSTALL"
  [ "$status" -eq 0 ]
  run bash "$INSTALL"
  [ "$status" -eq 0 ]
  grep -q '^PORT=8080$' "$HOME/.trug/.env"
}

@test "a key present but empty is replaced, not treated as already set" {
  # An empty env var beats config.yaml in trug's precedence, so TRUG_TOKEN_MCP=
  # is worse than the line being absent.
  run bash "$INSTALL"
  [ "$status" -eq 0 ]
  awk '!/^TRUG_TOKEN_MCP=/ { print } /^TRUG_TOKEN_MCP=/ { print "TRUG_TOKEN_MCP=" }' \
    "$HOME/.trug/.env" >"$TEST_TMP/env" && cp "$TEST_TMP/env" "$HOME/.trug/.env"

  run bash "$INSTALL"
  [ "$status" -eq 0 ]
  run grep -E '^TRUG_TOKEN_MCP=.+' "$HOME/.trug/.env"
  [ "$status" -eq 0 ]
}

@test "a .env with no trailing newline doesn't get its last value welded to a new key" {
  # An ordinary editor state. Appending blind turns TRUG_SECRET into
  # "abTRUG_ORIGIN=http://localhost:8000" and TRUG_ORIGIN never exists at all.
  mkdir -p "$HOME/.trug"
  printf 'PORT=8000\nTRUG_TOKEN_RING=ringtokenringtokenringtokenring1\nTRUG_SECRET=ab' \
    >"$HOME/.trug/.env"
  run bash "$INSTALL"
  [ "$status" -eq 0 ]
  grep -q '^TRUG_SECRET=ab$' "$HOME/.trug/.env"
  run grep -E '^TRUG_ORIGIN=http://localhost:8000$' "$HOME/.trug/.env"
  [ "$status" -eq 0 ]
}

@test "a whitespace-only value is repaired, not mistaken for a real one" {
  # An empty env var beats config.yaml, and " " is non-empty to every shell test
  # but useless to trug. A CRLF .env gives every value a trailing \r.
  mkdir -p "$HOME/.trug"
  printf 'PORT=8000\nTRUG_SECRET= \nTRUG_TOKEN_MCP=\n' >"$HOME/.trug/.env"
  run bash "$INSTALL"
  [ "$status" -eq 0 ]
  run grep -E '^TRUG_SECRET=[A-Za-z0-9]{32}$' "$HOME/.trug/.env"
  [ "$status" -eq 0 ]
  run grep -E '^TRUG_TOKEN_MCP=[A-Za-z0-9]{32}$' "$HOME/.trug/.env"
  [ "$status" -eq 0 ]
}

@test ".env is never briefly world-readable" {
  run bash "$INSTALL"
  [ "$status" -eq 0 ]
  perms="$(stat -f '%Lp' "$HOME/.trug/.env" 2>/dev/null || stat -c '%a' "$HOME/.trug/.env")"
  [ "$perms" = "600" ]
}

@test "a broken /dev/urandom is reported, not swallowed into a blank exit" {
  # die() inside $( ) exits only the subshell and its message lands in the
  # variable — the loudest possible failure turned into the quietest.
  cat >"$STUB_BIN/tr" <<'STUB'
#!/usr/bin/env bash
printf 'short'
STUB
  chmod +x "$STUB_BIN/tr"
  run bash "$INSTALL"
  [ "$status" -ne 0 ]
  [[ "$output" == *"random token"* ]]
}

@test "no curl: says so up front instead of blaming the port 90 seconds later" {
  hide_command curl
  run bash "$INSTALL"
  [ "$status" -ne 0 ]
  [[ "$output" == *"curl"* ]]
  [[ "$output" != *"never answered"* ]]
}

@test "an update run doesn't tell an established household to paste a dead token" {
  run bash "$INSTALL"
  [ "$status" -eq 0 ]
  boot="$(sed -n 's/^TRUG_BOOTSTRAP_TOKEN=//p' "$HOME/.trug/.env")"
  # A database on disk means the instance has been claimed; the token is inert
  # and the gate would answer "that token wasn't accepted".
  echo "pretend database" >"$HOME/.trug/data/trug.db"

  run bash "$INSTALL"
  [ "$status" -eq 0 ]
  [[ "$output" != *"$boot"* ]]
  [[ "$output" == *"sign in with your passkey"* ]]
}

@test "the PATH block is marked and written at most once" {
  echo 'export PATH="$HOME/.local/bin:$PATH"' >"$HOME/.zshrc"
  run bash "$INSTALL"
  [ "$status" -eq 0 ]
  run bash "$INSTALL"
  [ "$status" -eq 0 ]
  [ "$(grep -c 'trug installer' "$HOME/.zshrc" || true)" -le 1 ]
}

@test "health never comes up: says what to try rather than claiming success" {
  export CURL_STUB_EXIT=7
  export TRUG_HEALTH_TIMEOUT=2
  run bash "$INSTALL"
  [ "$status" -ne 0 ]
  # Points at `docker compose … logs`, not `trug logs`: the CLI is installed in
  # the step after this one, so it does not exist yet when this fires.
  [[ "$output" == *"logs --tail"* ]]
  [[ "$output" != *"trug logs"* ]]
}
