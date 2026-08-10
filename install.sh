#!/bin/sh
#
# trug installer — one command from nothing to a working shopping list.
#
#   curl -fsSL https://raw.githubusercontent.com/maxdraki/trug/main/install.sh | sh
#
# What it does: writes ~/.trug (compose file, .env, data volume), generates every
# token up front, pulls the image, starts it, waits until it answers, and drops a
# `trug` command on your PATH.
#
# Design notes worth not rediscovering:
#
# * POSIX sh, not bash. A Raspberry Pi's /bin/sh is dash, and `curl … | sh` runs
#   under whatever that is. Nothing here uses arrays, [[ ]], or ${var,,}.
# * Never blocks on stdin, and never touches fd 0. Piped from curl there is no
#   terminal to prompt at, so every decision is either automatic or an
#   environment variable. Every child gets `</dev/null` individually — a child
#   that reads stdin would otherwise swallow the rest of the script, since under
#   `curl … | sh` fd 0 is the pipe the shell is still reading itself from. For
#   the same reason nothing here may redirect fd 0 for the whole script:
#   `exec 0</dev/null` discards the shell's own source and the install becomes a
#   silent no-op on bash and a syntax error on dash.
# * Never installs Docker. Offering to install a container runtime unattended is
#   a bigger favour than anyone asked for; starting an app you already chose is
#   not, so a stopped Docker Desktop does get opened.
# * Idempotent. Re-running is the repair path: existing tokens, .env edits and
#   the database are all left exactly as they are.
#
# Environment overrides:
#   TRUG_HOME            install directory (default ~/.trug)
#   TRUG_PORT            host port (default 8000)
#   TRUG_REF             git ref to fetch the CLI from (default main)
#   TRUG_IMAGE           container image to run (default ghcr.io/maxdraki/trug:latest)
#   TRUG_LOCAL_SRC       install from a local checkout instead of GitHub
#   TRUG_HEALTH_TIMEOUT  seconds to wait for the first healthy response
#   NO_COLOR             set to anything to disable colour

set -eu

TRUG_HOME="${TRUG_HOME:-$HOME/.trug}"
# Whether the caller actually asked for a port, as opposed to taking the default
# — a re-run has to be able to move the port, and can't tell that from 8000.
if [ -n "${TRUG_PORT:-}" ]; then PORT_EXPLICIT=1; else PORT_EXPLICIT=0; fi
TRUG_PORT="${TRUG_PORT:-8000}"
TRUG_REF="${TRUG_REF:-main}"
TRUG_HEALTH_TIMEOUT="${TRUG_HEALTH_TIMEOUT:-90}"
# How long to wait for a Docker Desktop we started ourselves. Overridable only
# so the test suite doesn't sit through it on a machine that has Docker Desktop
# installed but stopped.
TRUG_DOCKER_START_TIMEOUT="${TRUG_DOCKER_START_TIMEOUT:-60}"
RAW_BASE="https://raw.githubusercontent.com/maxdraki/trug/${TRUG_REF}"
# Overridable so you can run an image you built or mirrored yourself. It is also
# what lets CI test the commit in front of it: the published :latest tag pulls
# successfully at any time, so without this the end-to-end job would install the
# last release instead of the change under review and pass either way.
IMAGE="${TRUG_IMAGE:-ghcr.io/maxdraki/trug:latest}"
BIN_DIR="$HOME/.local/bin"
STEP=0
TOTAL=5

# --- presentation -----------------------------------------------------------
# Colour only on a real terminal, and never when NO_COLOR is set. Everything
# below degrades to plain text in a CI log or a pipe.

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != "dumb" ]; then
  TTY=1
  BOLD=$(printf '\033[1m'); DIM=$(printf '\033[2m'); RESET=$(printf '\033[0m')
  GREEN=$(printf '\033[32m'); RED=$(printf '\033[31m'); YELLOW=$(printf '\033[33m')
  MAUVE=$(printf '\033[35m'); TEAL=$(printf '\033[36m')
  HIDE=$(printf '\033[?25l'); SHOW=$(printf '\033[?25h')
else
  TTY=0
  BOLD=''; DIM=''; RESET=''; GREEN=''; RED=''; YELLOW=''; MAUVE=''; TEAL=''
  HIDE=''; SHOW=''
fi

# Progress goes to stdout; anything that reports a problem goes to stderr, so
# `sh install.sh > install.log` still shows the user why it stopped.
ok()   { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$*"; }
warn() { printf '  %s!%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
bad()  { printf '  %s✗%s %s\n' "$RED" "$RESET" "$*" >&2; }
hint() { printf '    %s%s%s\n' "$DIM" "$*" "$RESET" >&2; }

# Erase to end of line — only meaningful on a terminal, and it shows up as a
# literal "[K" in a log file otherwise.
clear_line() { if [ "$TTY" = 1 ]; then printf '\r\033[K'; fi; }

# Restore the cursor however we leave — a spinner that dies mid-frame otherwise
# hands back a terminal with no cursor, which reads as a hung machine.
cleanup() {
  # An `if`, not a `&&` chain: under `set -e` a trap whose first command returns
  # non-zero aborts the rest of the trap AND leaves the script exiting 1 after a
  # completely successful run.
  if [ "$TTY" = 1 ]; then printf '%s' "$SHOW"; fi
}
trap cleanup EXIT INT TERM

banner() {
  printf '\n'
  printf '  %s%s ┌┬┐┬─┐┬ ┬┌─┐%s\n' "$BOLD" "$MAUVE" "$RESET"
  printf '  %s%s  │ ├┬┘│ ││ ┬%s\n' "$BOLD" "$MAUVE" "$RESET"
  printf '  %s%s  ┴ ┴└─└─┘└─┘%s   %sa shared shopping list, on your own machine%s\n' \
    "$BOLD" "$MAUVE" "$RESET" "$DIM" "$RESET"
  printf '\n'
}

step() {
  STEP=$((STEP + 1))
  printf '\n%s[%s/%s]%s %s%s%s\n' "$DIM" "$STEP" "$TOTAL" "$RESET" "$BOLD" "$1" "$RESET"
}

die() {
  bad "$1"
  shift
  for line in "$@"; do hint "$line"; done
  printf '\n'
  exit 1
}

# Run a command with a spinner, hiding its output unless it fails. Frames are
# separate words rather than one string because POSIX parameter expansion slices
# bytes, not characters, and would shred a multi-byte glyph.
spin() {
  label="$1"; shift
  log="$TMP_DIR/step.log"
  if [ "$TTY" != 1 ]; then
    printf '  … %s\n' "$label"
    if "$@" >"$log" 2>&1 </dev/null; then ok "$label"; return 0; fi
    bad "$label"; sed 's/^/    /' "$log"; return 1
  fi
  printf '%s' "$HIDE"
  "$@" >"$log" 2>&1 </dev/null &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    for frame in '⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏'; do
      kill -0 "$pid" 2>/dev/null || break
      printf '\r  %s%s%s %s' "$TEAL" "$frame" "$RESET" "$label"
      sleep 0.08
    done
  done
  set +e; wait "$pid"; rc=$?; set -e
  clear_line
  printf '%s' "$SHOW"
  if [ "$rc" -eq 0 ]; then ok "$label"; return 0; fi
  bad "$label"
  sed 's/^/    /' "$log"
  return "$rc"
}

# --- token generation --------------------------------------------------------

# 32 alphanumeric characters. Alphanumeric on purpose: these travel raw in a
# `?token=` URL and through Compose's variable interpolation, so anything that
# needs escaping in either place is a bug waiting for a Saturday.
#
# `dd`, not `head -c`: head closes the pipe as soon as it has its bytes, which
# kills `tr` with SIGPIPE and makes the whole pipeline fail under `set -e`.
# `bs=1 count=32`, not `bs=32 count=1`: the latter is a single read() on a pipe,
# which is allowed to come back short and would fail the check below on a
# perfectly healthy machine.
gen_token() {
  LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | dd bs=1 count=32 2>/dev/null
}

# True when a value is exactly 32 characters. A short or empty value would be
# written as `TRUG_TOKEN_MCP=`, and an empty environment variable BEATS
# config.yaml in trug's config precedence — strictly worse than being absent.
#
# This *reports* rather than dying, because the caller has to be the one to die:
# every use is `x="$(gen_token)"`, and an `exit` inside a command substitution
# kills only the subshell while the error text is captured into the variable.
# That produced the worst possible outcome — the installer stopping mid-step
# with a blank screen — from the guard meant to make the failure loud.
token_ok() {
  case "$1" in
    ????????????????????????????????) return 0 ;;
    *) return 1 ;;
  esac
}

no_randomness() {
  die "Couldn't generate a random token." \
      "/dev/urandom didn't return 32 usable bytes, which shouldn't happen." \
      "Check that \`tr\` and \`dd\` work, then please open an issue with your" \
      "OS and shell version."
}

# --- file writers ------------------------------------------------------------

# Read one KEY=value out of an existing .env, or print nothing.
#
# Deliberately duplicated in bin/trug rather than shared: this script is fetched
# on its own and piped straight to a shell, so it cannot source anything. Keep
# the two in step by hand — three lines is a cheaper price than a curl pipeline
# that depends on a second download.
# The LAST match, matching Compose's `env_file` precedence — see bin/trug's copy.
env_value() {
  [ -f "$2" ] || return 0
  sed -n "s/^$1=//p" "$2" | tail -n 1
}

# Remove a key that is present but has no value. An empty environment variable
# beats config.yaml in trug's config precedence, so `TRUG_TOKEN_MCP=` is worse
# than no line at all — and it would otherwise look "already set" to ensure_key.
drop_empty_key() {
  # Whitespace counts as empty. `TRUG_SECRET= ` is a valid non-empty variable
  # that beats config.yaml in trug's precedence, so it silently wins over the
  # real value — and a .env edited on Windows gives every key a trailing \r.
  grep -qE "^${2}=[[:space:]]*$" "$1" 2>/dev/null || return 0
  # Write beside the original and rename, never `cat tmp > original`: that
  # truncates the live file first and streams second, so a full disk (a Pi with
  # a full SD card is the ordinary case) leaves .env half-written with
  # TRUG_SECRET gone and unrecoverable. Same directory, so the rename is atomic.
  tmp="${1}.tmp.$$"
  # `grep -v` exits 1 when it prints nothing, which for a .env whose only line
  # is the empty key would leave it in place forever — so the emptiness of the
  # result is judged here, not by grep's status.
  grep -vE "^${2}=[[:space:]]*$" "$1" >"$tmp" 2>/dev/null || true
  if [ -s "$tmp" ]; then
    chmod 600 "$tmp"
    mv "$tmp" "$1"
  else
    : >"$1"
    rm -f "$tmp"
  fi
}

# Replace a key's value in place, atomically. Only used for the handful of
# settings the caller can legitimately change on a re-run.
replace_key() {
  target="$1"; key="$2"; value="$3"
  tmp="${target}.tmp.$$"
  if awk -v k="$key" -v v="$value" \
       'index($0, k "=") == 1 { print k "=" v; next } { print }' \
       "$target" >"$tmp" && [ -s "$tmp" ] && grep -q "^${key}=" "$tmp"; then
    chmod 600 "$tmp"
    mv "$tmp" "$target"
  else
    rm -f "$tmp"
    die "Couldn't update $key in $target — nothing was changed." \
        "Check there is free space on the disk, then try again."
  fi
}

# Append `KEY=value` unless the key is already present. Never rewrites, never
# reorders, never drops a line it doesn't recognise.
ensure_key() {
  target="$1"; key="$2"; value="$3"
  drop_empty_key "$target" "$key"
  grep -q "^${key}=" "$target" 2>/dev/null && return 0
  # A .env with no final newline is an ordinary editor state, and appending to
  # one welds the new key onto the end of the last value: TRUG_SECRET becomes
  # "abTRUG_ORIGIN=http://localhost:8000" and TRUG_ORIGIN never exists, while
  # the installer reports that everything was left alone.
  if [ -s "$target" ] && [ -n "$(tail -c 1 "$target")" ]; then
    printf '\n' >>"$target"
  fi
  printf '%s=%s\n' "$key" "$value" >>"$target"
}

# Generate a token into $NEW_TOKEN, dying in the CALLER's shell on failure.
# Assigning to a global instead of echoing is what keeps the `die` reachable —
# see token_ok above.
generate_token() {
  NEW_TOKEN="$(gen_token)"
  token_ok "$NEW_TOKEN" || no_randomness
}

# Write .env on a first install; on a re-run, only fill in what is missing.
#
# The first version of this rebuilt the file from a fixed template, which read
# fine until you noticed it deleted every setting the installer doesn't know
# about. Someone behind a proxy sets TRUG_TRUSTED_PROXY_HOPS=1, re-runs the
# installer a month later to update, and silently goes back to one shared
# rate-limit bucket for the whole internet — with the doctor reporting healthy,
# because the config it sees is valid. Same for LLM_API_KEY, TRUG_SESSION_DAYS,
# and any comment the operator wrote to remind themselves why.
#
# Preserving existing values also protects the four secrets: TRUG_SECRET
# decrypts the stored LLM key, and the machine tokens are live credentials in
# someone's ring and MCP client, so regenerating any of them turns a working
# instance into a broken one.
write_env() {
  target="$1"

  # umask, not a later chmod: `cat > file` creates it 0644 with a default umask,
  # so a template written first and chmod'd second leaves four secrets briefly
  # world-readable — and permanently so if anything interrupts in between.
  old_umask="$(umask)"
  umask 077

  if [ ! -f "$target" ]; then
    generate_token; ring="$NEW_TOKEN"
    generate_token; mcp="$NEW_TOKEN"
    generate_token; boot="$NEW_TOKEN"
    generate_token; secret="$NEW_TOKEN"
    # Written beside the target and renamed, so an interrupted or out-of-space
    # write never leaves a partial .env behind. A partial one is worse than
    # none: the next run sees the file, takes FIRST_RUN=0, finds a two-character
    # TRUG_SECRET non-empty, keeps it, and reports that everything is fine.
    cat >"$target.new" <<ENV
# trug configuration. Written by the installer; safe to edit by hand — re-running
# the installer only fills in what's missing and never rewrites what's here.
#
# Every setting: https://github.com/maxdraki/trug/blob/main/docs/configuration.md

# The address people load, and the bare host passkeys are bound to. Change these
# with \`trug set-origin https://your-name\` rather than by hand: it derives the
# RP ID, restarts, and checks the result.
TRUG_ORIGIN=http://localhost:$TRUG_PORT
TRUG_RP_ID=localhost

PORT=$TRUG_PORT

# Machine credentials. TRUG_TOKEN_MCP is the one \`trug share\` hands out.
TRUG_TOKEN_RING=$ring
TRUG_TOKEN_MCP=$mcp

# Spent once, on the first account. Inert afterwards.
TRUG_BOOTSTRAP_TOKEN=$boot

# Encrypts an LLM key stored from in-app Settings. Changing it makes that key
# unreadable, so the installer never regenerates it.
TRUG_SECRET=$secret
ENV
    chmod 600 "$target.new"
    mv "$target.new" "$target"
  else
    port="$(env_value PORT "$target")"; [ -n "$port" ] || port="$TRUG_PORT"
    # An explicitly-passed TRUG_PORT wins over what's already in .env. Without
    # this the port is fixed at first install and the advice printed when the
    # health wait times out — "the port is probably taken, re-run with
    # TRUG_PORT=8080" — provably cannot work: the re-run keeps the old value and
    # fails identically.
    if [ "$PORT_EXPLICIT" = 1 ] && [ "$port" != "$TRUG_PORT" ]; then
      old_origin="$(env_value TRUG_ORIGIN "$target")"
      replace_key "$target" PORT "$TRUG_PORT"
      # Move the origin with it, but only while it is still the default shape.
      # A real origin someone set by hand is theirs, not ours to rewrite.
      if [ "$old_origin" = "http://localhost:$port" ]; then
        replace_key "$target" TRUG_ORIGIN "http://localhost:$TRUG_PORT"
      fi
      port="$TRUG_PORT"
    fi
    ensure_key "$target" PORT "$port"
    ensure_key "$target" TRUG_ORIGIN "http://localhost:$port"
    ensure_key "$target" TRUG_RP_ID localhost
    for key in TRUG_TOKEN_RING TRUG_TOKEN_MCP TRUG_BOOTSTRAP_TOKEN TRUG_SECRET; do
      # Whitespace counts as missing, so the test strips it first. `TRUG_SECRET= `
      # is non-empty to every shell test and useless to trug — and it beats
      # config.yaml, so it silently wins over the real value. A .env edited on
      # Windows gives every value a trailing \r, which would otherwise look set.
      # A pinned token someone chose themselves is left alone whatever its
      # shape: this fills gaps, it does not police values.
      # LC_ALL=C, like gen_token's tr: BSD tr aborts with "Illegal byte sequence"
      # on invalid multibyte input in a UTF-8 locale. A .env hand-edited in
      # Latin-1 would kill it here, the substitution would come back empty, and
      # the installer would conclude the key is missing and regenerate
      # TRUG_SECRET — which is documented above as unrecoverable.
      if [ -z "$(env_value "$key" "$target" | LC_ALL=C tr -d '[:space:]')" ]; then
        generate_token
        ensure_key "$target" "$key" "$NEW_TOKEN"
      fi
    done
  fi

  chmod 600 "$target"
  umask "$old_umask"
}

# The emitted compose file differs from the repo's in three deliberate ways:
# no `build:` (nobody installing this wants a multi-minute source build), an
# explicit project and container name (so `docker ps` is legible and a repo
# clone elsewhere can't collide), and the short `env_file:` form (the long form
# needs Compose v2.24+, and a Debian box can easily have older).
write_compose() {
  cat >"$1" <<COMPOSE
# Written by the trug installer. Safe to edit.
name: trug
services:
  trug:
    image: $IMAGE
    container_name: trug
    # Published on every interface on purpose: the point of a household list is
    # the other phone in the house. \`trug share\` prints the link for it.
    ports:
      - "\${PORT:-8000}:\${PORT:-8000}"
    env_file: .env
    volumes:
      - ./data:/data
    restart: unless-stopped
COMPOSE
}

# Everything above is reusable; sourcing with TRUG_LIB_ONLY=1 stops here so the
# test suite can exercise the writers without installing anything.
[ -n "${TRUG_LIB_ONLY:-}" ] && return 0

# --- preflight ---------------------------------------------------------------

TMP_DIR="$(mktemp -d)"
trap 'cleanup; rm -rf "$TMP_DIR"' EXIT
# INT/TERM need their own trap ending in `exit`: a POSIX shell RESUMES after a
# handler that returns, so without this Ctrl-C during the 90-second health wait
# restores the cursor and then carries on, leaving no way out but closing the
# terminal. 130 is the conventional "killed by SIGINT" status.
trap 'cleanup; rm -rf "$TMP_DIR"; exit 130' INT TERM

banner
step "Checking this machine"

case "$(uname -m 2>/dev/null || echo unknown)" in
  armv6l|armv7l|armv8l|armhf|i386|i686)
    # armv8l is the one people miss: 32-bit Raspberry Pi OS sets arm_64bit=1 by
    # default on a Pi 4/400/CM4, so it runs a 64-bit kernel under a 32-bit
    # userland and reports armv8l rather than armv7l. Same outcome, and without
    # this it sails past the guard into Docker's own error message.
    die "This is a 32-bit userland ($(uname -m))." \
        "trug's image is 64-bit only (amd64 and arm64), so the pull would fail" \
        "with 'no matching manifest'." \
        "On a Pi 3, 4, 5 or Zero 2, reflash with the 64-bit Raspberry Pi OS —" \
        "a 64-bit kernel is not enough, the userland has to be 64-bit too." ;;
esac
ok "Architecture $(uname -m) is supported"

# `docker --version`, not `command -v docker`: a wrapper on PATH that can't
# actually run (a broken shim, a half-removed Docker Desktop) is not installed
# as far as anyone using it is concerned, and saying so beats failing later with
# a daemon message that sends them to start something that isn't there.
if ! docker --version >/dev/null 2>&1 </dev/null; then
  die "Docker isn't installed." \
      "trug is one container, so it needs a container runtime. Any of these:" \
      "  Docker Desktop  https://docker.com/products/docker-desktop  (Mac, Windows)" \
      "  OrbStack        https://orbstack.dev                        (Mac, lighter)" \
      "  Colima          brew install colima docker                  (Mac, terminal)" \
      "  Docker Engine   https://docs.docker.com/engine/install/     (Linux, a Pi)" \
      "Then run this installer again."
fi

if ! docker info >/dev/null 2>&1 </dev/null; then
  # A stopped Docker Desktop is worth starting — the user already chose to
  # install it. Anything else is theirs to sort out.
  if [ -d "/Applications/Docker.app" ] && command -v open >/dev/null 2>&1; then
    warn "Docker is installed but not running — starting Docker Desktop"
    open -a Docker >/dev/null 2>&1 </dev/null || true
    waited=0
    while [ "$waited" -lt "$TRUG_DOCKER_START_TIMEOUT" ]; do
      docker info >/dev/null 2>&1 </dev/null && break
      sleep 2
      waited=$((waited + 2))
      printf '\r    %swaiting for Docker to start (%ss)%s' "$DIM" "$waited" "$RESET"
    done
    clear_line
  fi
fi

if ! docker info >/dev/null 2>&1 </dev/null; then
  die "Docker is installed but the daemon isn't running." \
      "Start Docker Desktop (or OrbStack, or \`colima start\`) and run this again." \
      "On Linux: sudo systemctl start docker" \
      "If that says 'permission denied', add yourself to the docker group:" \
      "  sudo usermod -aG docker \$USER   — then log out and back in."
fi
ok "Docker is running"

if ! docker compose version >/dev/null 2>&1 </dev/null; then
  die "Docker Compose v2 isn't available." \
      "\`docker compose version\` failed. On Debian/Raspberry Pi OS:" \
      "  sudo apt install docker-compose-plugin" \
      "The standalone \`docker-compose\` v1 script is not a substitute."
fi
ok "Docker Compose v2 is available"

# Checked here rather than discovered at the health probe. Without this, a box
# with no curl (a minimal Debian, or anyone who fetched this with `wget -qO- |
# sh`) waits the full timeout and is then told trug never answered — sending
# them to hunt a port conflict that doesn't exist.
if ! curl --version >/dev/null 2>&1 </dev/null; then
  die "curl isn't installed." \
      "It's needed to check that trug actually answers once it starts." \
      "  Debian / Raspberry Pi OS:  sudo apt install curl" \
      "  Fedora:                    sudo dnf install curl"
fi

# --- layout ------------------------------------------------------------------

step "Setting up $TRUG_HOME"

FIRST_RUN=1
[ -f "$TRUG_HOME/.env" ] && FIRST_RUN=0

mkdir -p "$TRUG_HOME/data" "$TRUG_HOME/backups"
write_env "$TRUG_HOME/.env"
write_compose "$TRUG_HOME/docker-compose.yml"

if [ "$FIRST_RUN" = 1 ]; then
  ok "Created $TRUG_HOME with fresh tokens"
else
  ok "Found an existing install — tokens, settings and data left alone"
fi

# Only offer the bootstrap token when it can actually be spent. Once an account
# exists the token is inert: the server flat-403s the claim, and the gate says
# "that token wasn't accepted" — so printing it on an update run tells a
# household of a month's standing to do something that visibly fails.
CLAIMED=0
if [ "$FIRST_RUN" = 0 ] && [ -f "$TRUG_HOME/data/trug.db" ]; then CLAIMED=1; fi

PORT="$(env_value PORT "$TRUG_HOME/.env")"
BOOT_TOKEN="$(env_value TRUG_BOOTSTRAP_TOKEN "$TRUG_HOME/.env")"

# --- image -------------------------------------------------------------------

step "Fetching trug"

if ! spin "Pulling $IMAGE" docker compose --project-directory "$TRUG_HOME" pull; then
  # A failed pull is only fatal when there is nothing to run. If the image is
  # already in the local daemon — a repair run on a flaky connection, a Pi that
  # is offline this morning, or an image built locally — starting the copy that
  # is here is strictly better than refusing to start at all.
  if docker image inspect "$IMAGE" >/dev/null 2>&1 </dev/null; then
    warn "Couldn't reach the registry — using the copy already on this machine"
  else
    die "Couldn't pull the image." \
        "Check your connection, then run this installer again." \
        "The output above is from Docker."
  fi
fi

step "Starting it"

if ! spin "Starting the container" docker compose --project-directory "$TRUG_HOME" up -d; then
  die "The container didn't start." \
      "Run \`trug logs\` — or \`docker compose --project-directory $TRUG_HOME logs\` —" \
      "to see what Docker said."
fi

# Wait for a real answer rather than trusting `up -d`, which returns as soon as
# the container is created and says nothing about whether the app works.
printf '  %swaiting for trug to answer%s ' "$DIM" "$RESET"
waited=0
healthy=0
while [ "$waited" -lt "$TRUG_HEALTH_TIMEOUT" ]; do
  if curl -fsS -o /dev/null "http://localhost:$PORT/healthz" 2>/dev/null </dev/null; then
    healthy=1
    break
  fi
  sleep 1
  waited=$((waited + 1))
  printf '.'
done
clear_line

if [ "$healthy" != 1 ]; then
  # Say what is true (no answer) and let the container's own state pick the
  # likely cause, rather than asserting a port conflict that is often not it.
  if docker compose --project-directory "$TRUG_HOME" ps --status running -q trug 2>/dev/null </dev/null | grep -q .; then
    die "trug is running but didn't answer on port $PORT within ${TRUG_HEALTH_TIMEOUT}s." \
        "The container is up, so this is usually the app failing to start." \
        "  docker compose --project-directory $TRUG_HOME logs --tail 40" \
        "If port $PORT is taken by something else, install on another one:" \
        "  curl -fsSL $RAW_BASE/install.sh | TRUG_PORT=8080 sh"
  fi
  die "trug started and then stopped." \
      "  docker compose --project-directory $TRUG_HOME logs --tail 40" \
      "A port already in use is the usual cause. To use another one:" \
      "  curl -fsSL $RAW_BASE/install.sh | TRUG_PORT=8080 sh"
fi
ok "trug is answering on port $PORT"

# --- the CLI -----------------------------------------------------------------

step "Installing the trug command"

mkdir -p "$BIN_DIR"
if [ -n "${TRUG_LOCAL_SRC:-}" ]; then
  cp "$TRUG_LOCAL_SRC/bin/trug" "$TMP_DIR/trug"
else
  if ! curl -fsSL "$RAW_BASE/bin/trug" -o "$TMP_DIR/trug" </dev/null; then
    die "Couldn't download the trug command from $RAW_BASE/bin/trug." \
        "trug itself is running — this is just the convenience wrapper." \
        "You can manage it with: docker compose --project-directory $TRUG_HOME ..."
  fi
fi

# Verify what arrived is a script before making it executable: a captive portal
# or a 404 page served as 200 would otherwise become an executable on PATH.
case "$(head -n 1 "$TMP_DIR/trug")" in
  '#!'*) ;;
  *) die "The downloaded trug command doesn't look like a script." \
         "Refusing to install it. This usually means a network captive portal" \
         "intercepted the download — reconnect and try again." ;;
esac

mv "$TMP_DIR/trug" "$BIN_DIR/trug"
chmod +x "$BIN_DIR/trug"
ok "Installed $BIN_DIR/trug"

# Add ~/.local/bin to PATH if it isn't there. Marked so a second run finds its
# own block instead of appending another, and never with sudo.
case ":$PATH:" in
  *":$BIN_DIR:"*) ON_PATH=1 ;;
  *) ON_PATH=0 ;;
esac

if [ "$ON_PATH" = 0 ]; then
  for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
    [ -f "$rc" ] || continue
    if ! grep -q 'trug installer' "$rc" 2>/dev/null; then
      {
        printf '\n# Added by the trug installer\n'
        # shellcheck disable=SC2016  # $HOME must stay literal in the rc file
        printf 'export PATH="$HOME/.local/bin:$PATH"\n'
      } >>"$rc"
      ok "Added $BIN_DIR to PATH in $(basename "$rc")"
    fi
  done
  warn "Open a new terminal (or run: export PATH=\"\$HOME/.local/bin:\$PATH\") to use \`trug\`"
fi

# --- done --------------------------------------------------------------------

# Which address other devices should use. Mirrors bin/trug's lan_ip() and must
# stay in step with it — see env_value above for why they aren't shared.
#
# A private address on a real interface FIRST, then the routing table. Both
# fallbacks exist because both ways of guessing are wrong on a real machine:
# `ip route get` returns the VPN's source whenever a VPN carries the default
# route (a Tailscale exit node — and Tailscale is what the closing message
# recommends next), and `hostname -I` lists every address including docker0.
# Either produces the worst kind of failure: a link that looks right and times
# out from every phone in the house.
#
# This drifted from bin/trug's lan_ip() once already, while the comment above
# claimed it hadn't — a review caught it. Change both or neither.
LAN_IP=''
if command -v ipconfig >/dev/null 2>&1; then
  LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
fi
if [ -z "$LAN_IP" ] && command -v ip >/dev/null 2>&1; then
  LAN_IP="$(ip -4 -o addr show scope global 2>/dev/null \
    | awk '$2 !~ /^(docker|br-|veth|virbr|tailscale|wg|tun|zt|lo)/ { print $4 }' \
    | cut -d/ -f1 \
    | grep -E '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)' \
    | head -n 1 || true)"
fi
if [ -z "$LAN_IP" ] && command -v ip >/dev/null 2>&1; then
  LAN_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' || true)"
fi
if [ -z "$LAN_IP" ] && command -v hostname >/dev/null 2>&1; then
  # Skip docker bridges, loopback and link-local rather than taking the first.
  LAN_IP="$(hostname -I 2>/dev/null | tr ' ' '\n' \
    | grep -vE '^(127\.|169\.254\.|172\.1[7-9]\.|172\.2[0-9]\.|172\.3[01]\.)' \
    | head -n 1 || true)"
fi

printf '\n  %s%s✓ trug is up.%s\n\n' "$BOLD" "$GREEN" "$RESET"
printf '  %sTwo ways in, and they behave differently.%s\n\n' "$BOLD" "$RESET"

printf '  %s1. localhost — with accounts%s\n' "$BOLD" "$RESET"
printf '     %shttp://localhost:%s%s\n' "$TEAL" "$PORT" "$RESET"
printf '     %sOnly on this machine. Your browser will ask for a passkey (Touch ID,%s\n' "$DIM" "$RESET"
printf '     %sWindows Hello, a security key) — that is the point: it is the one%s\n' "$DIM" "$RESET"
printf '     %saddress where an account can be created at all.%s\n\n' "$DIM" "$RESET"

if [ -n "$LAN_IP" ]; then
  printf '  %s2. the network — no accounts, no passkey%s\n' "$BOLD" "$RESET"
  printf '     %shttp://%s:%s%s\n' "$TEAL" "$LAN_IP" "$PORT" "$RESET"
  printf '     %sRun %strug share%s%s for that link with the key already in it, plus a QR.%s\n' \
    "$DIM" "$BOLD" "$RESET" "$DIM" "$RESET"
  printf '     %sNo sign-in prompt, nothing to install. Everyone who opens it shares%s\n' "$DIM" "$RESET"
  printf '     %sONE identity — the list is shared, the login is not. Items show up as%s\n' "$DIM" "$RESET"
  printf '     %s"mcp" rather than by name, and there are no per-person settings.%s\n' "$DIM" "$RESET"
  printf '     %sBrowsers refuse to make a passkey against an IP, which is why this%s\n' "$DIM" "$RESET"
  printf '     %sroute skips them rather than choosing not to use them.%s\n\n' "$DIM" "$RESET"
fi

printf '  %sNext%s\n' "$BOLD" "$RESET"
if [ "$CLAIMED" = 1 ]; then
  printf '   1. Open %shttp://localhost:%s%s and sign in with your passkey.\n' "$TEAL" "$PORT" "$RESET"
else
  printf '   1. Open %shttp://localhost:%s%s, choose "use an access token", and paste:\n' "$TEAL" "$PORT" "$RESET"
  printf '      %s%s%s\n' "$BOLD" "$BOOT_TOKEN" "$RESET"
  printf '      %sThen pick a name and create your passkey.%s\n' "$DIM" "$RESET"
  printf '      %sUntil you do, anything that can reach this machine can claim it.%s\n' "$DIM" "$RESET"
fi
printf '   2. %strug share%s — the link and QR for everyone else in the house.\n' "$BOLD" "$RESET"
printf '   3. When you want real accounts on every phone, give the box an HTTPS\n'
printf '      name and tell trug about it: %strug set-origin https://…%s\n' "$BOLD" "$RESET"
printf '\n  %sup · down · status · logs · share · set-origin · doctor%s\n' "$DIM" "$RESET"
printf '  %s%s%s\n\n' "$DIM" "$TRUG_HOME" "$RESET"
