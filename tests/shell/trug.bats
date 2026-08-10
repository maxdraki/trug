#!/usr/bin/env bats
#
# bin/trug — the six verbs. `share` and `set-origin` carry almost all the risk:
# one hands out a credential, the other is the graduation edit whose failure mode
# is silent.

load helpers

setup() {
  setup_sandbox
  stub_docker
  stub_curl
  make_installed_home
  TRUG="$REPO_ROOT/bin/trug"
}

teardown() { teardown_sandbox; }

@test "runs under bash 3.2, which is what macOS still ships" {
  # The rest of the suite runs under whatever bash is first on PATH — usually 5.x
  # from Homebrew. Nothing else stops a ${x,,} or a declare -A shipping green and
  # then failing on every stock Mac.
  [ -x /bin/bash ] || skip "no /bin/bash"
  case "$(/bin/bash --version | head -n 1)" in
    *"version 3."*) ;;
    *) skip "/bin/bash is not 3.x here" ;;
  esac
  run /bin/bash "$TRUG" share
  [ "$status" -eq 0 ]
  assert_contains "?token=" "$output"
}

@test "no arguments: prints usage and fails, rather than guessing a verb" {
  run bash "$TRUG"
  [ "$status" -ne 0 ]
  assert_contains "share" "$output"
  assert_contains "set-origin" "$output"
}

@test "unknown verb: names it instead of a generic usage dump" {
  run bash "$TRUG" frobnicate
  [ "$status" -ne 0 ]
  assert_contains "frobnicate" "$output"
}

@test "not installed: points at the installer instead of a confusing compose error" {
  rm -rf "$HOME/.trug"
  run bash "$TRUG" status
  [ "$status" -ne 0 ]
  assert_contains "install.sh" "$output"
}

@test "up / down / logs pass through to compose in the right directory" {
  run bash "$TRUG" up
  [ "$status" -eq 0 ]
  grep -q -- "--project-directory $HOME/.trug up -d" "$DOCKER_LOG"

  run bash "$TRUG" down
  [ "$status" -eq 0 ]
  grep -q -- "--project-directory $HOME/.trug down" "$DOCKER_LOG"
}

@test "share: prints the LAN URL carrying the MCP token" {
  run bash "$TRUG" share
  [ "$status" -eq 0 ]
  assert_contains "http://192.168.1.42:8000/?token=mcptokenmcptokenmcptokenmcptoke1" "$output"
}

@test "share: a duplicated key resolves the way Compose resolves it — last wins" {
  # Appending a line to override an earlier one is an ordinary hand-edit, and it
  # genuinely works, because `env_file` takes the last value. So the link has to
  # carry the value the *running server* has: taking the first would print a
  # token the server rejects, with both sides looking correct.
  echo "TRUG_TOKEN_MCP=secondvaluesecondvaluesecondva2" >>"$HOME/.trug/.env"
  echo "PORT=8090" >>"$HOME/.trug/.env"
  run bash "$TRUG" share
  [ "$status" -eq 0 ]
  assert_contains "http://192.168.1.42:8090/?token=secondvaluesecondvaluesecondva2" "$output"
  refute_contains "mcptokenmcptokenmcptokenmcptoke1" "$output"
  # And exactly one URL: a reader returning both values would splice them
  # together, newline and all, into the link and the QR built from it.
  url="$(printf '%s\n' "$output" | grep -o 'http://[^ ]*?token=[^ ]*')"
  [ "$(printf '%s\n' "$url" | wc -l | tr -d ' ')" = "1" ]
}

@test "share: never prints the bootstrap token, which is a different power" {
  run bash "$TRUG" share
  refute_contains "boottokenboottokenboottokenboot1" "$output"
}

@test "share: warns that ufw is NOT protecting this port, and never says to allow it" {
  # Docker publishes container ports by DNAT + FORWARD; ufw filters INPUT, which
  # that traffic never touches. So `ufw allow` is a no-op here — and someone with
  # a default-deny policy will wrongly believe this link is unreachable.
  cat >"$STUB_BIN/uname" <<'STUB'
#!/usr/bin/env bash
[ "$1" = "-s" ] && { echo Linux; exit 0; }
exec /usr/bin/uname "$@"
STUB
  cat >"$STUB_BIN/systemctl" <<'STUB'
#!/usr/bin/env bash
[ "${3:-}" = "ufw" ] && exit 0
exit 3
STUB
  chmod +x "$STUB_BIN/uname" "$STUB_BIN/systemctl"
  run bash "$TRUG" share
  [ "$status" -eq 0 ]
  assert_contains "not protecting this port" "$output"
  assert_contains "DOCKER-USER" "$output"
  # The advice that was shipped and was wrong.
  refute_contains "ufw allow" "$output"
}

@test "share: stays quiet about firewalls when none is running" {
  cat >"$STUB_BIN/uname" <<'STUB'
#!/usr/bin/env bash
[ "$1" = "-s" ] && { echo Linux; exit 0; }
exec /usr/bin/uname "$@"
STUB
  cat >"$STUB_BIN/systemctl" <<'STUB'
#!/usr/bin/env bash
exit 3
STUB
  chmod +x "$STUB_BIN/uname" "$STUB_BIN/systemctl"
  run bash "$TRUG" share
  [ "$status" -eq 0 ]
  refute_contains "ufw" "$output"
  refute_contains "firewall-cmd" "$output"
}

@test "share: prefers a real LAN address over a VPN or a docker bridge" {
  # `ip route get` returns the VPN's source whenever the VPN carries the default
  # route — an address that answers from this box and from nowhere else.
  cat >"$STUB_BIN/ipconfig" <<'STUB'
#!/usr/bin/env bash
exit 1
STUB
  cat >"$STUB_BIN/ip" <<'STUB'
#!/usr/bin/env bash
if [ "$2" = "-o" ] || [ "$3" = "addr" ]; then
  echo "1: lo    inet 127.0.0.1/8 scope host lo"
  echo "2: docker0    inet 172.17.0.1/16 scope global docker0"
  echo "3: tailscale0    inet 100.101.102.103/32 scope global tailscale0"
  echo "4: enp3s0    inet 192.168.1.42/24 scope global enp3s0"
  exit 0
fi
echo "1.1.1.1 via 100.64.0.1 dev tailscale0 src 100.101.102.103"
exit 0
STUB
  chmod +x "$STUB_BIN/ipconfig" "$STUB_BIN/ip"
  run bash "$TRUG" share
  [ "$status" -eq 0 ]
  assert_contains "http://192.168.1.42:8000/" "$output"
  refute_contains "100.101.102.103" "$output"
  refute_contains "172.17.0.1" "$output"
}

@test "share: says 'this network', because a laptop travels" {
  run bash "$TRUG" share
  assert_contains "Anyone on this network" "$output"
}

@test "share: renders the QR when the terminal has room" {
  export COLUMNS=100
  run bash "$TRUG" share
  assert_contains "[QR-BLOCK]" "$output"
}

@test "share: a narrow terminal gets the plain URL, not a wrapped QR" {
  export DOCKER_STUB_QR=too-narrow
  run bash "$TRUG" share
  [ "$status" -eq 0 ]
  refute_contains "[QR-BLOCK]" "$output"
  assert_contains "?token=" "$output"
}

@test "share: an image too old to render a QR doesn't spill a python error" {
  # Upgrading the CLI without the image is an ordinary thing to do. The link
  # still works, so the QR failing is not worth a traceback.
  export DOCKER_STUB_QR=no-module
  run bash "$TRUG" share
  [ "$status" -eq 0 ]
  assert_contains "?token=" "$output"
  refute_contains "No module named" "$output"
  refute_contains "Traceback" "$output"
}

@test "share: a stopped container is refused before any link is printed" {
  # Handing out a link and a QR that nothing is listening on is the one failure
  # this command must never produce — the phone times out while every line on
  # screen says it should have worked.
  export DOCKER_STUB_RUNNING=no
  run bash "$TRUG" share
  [ "$status" -ne 0 ]
  assert_contains "trug up" "$output"
  refute_contains "?token=" "$output"
}

@test "set-origin: writes the origin and derives a bare-host RP ID" {
  run bash "$TRUG" set-origin https://trug.tail1234.ts.net
  [ "$status" -eq 0 ]
  grep -q '^TRUG_ORIGIN=https://trug.tail1234.ts.net$' "$HOME/.trug/.env"
  grep -q '^TRUG_RP_ID=trug.tail1234.ts.net$' "$HOME/.trug/.env"
}

@test "set-origin: strips the port from the RP ID, which is the mistake the doctor exists for" {
  run bash "$TRUG" set-origin https://trug.example.com:8443
  [ "$status" -eq 0 ]
  grep -q '^TRUG_RP_ID=trug.example.com$' "$HOME/.trug/.env"
  grep -q '^TRUG_ORIGIN=https://trug.example.com:8443$' "$HOME/.trug/.env"
}

@test "set-origin: refuses a bare hostname, which would silently break every ceremony" {
  run bash "$TRUG" set-origin trug.example.com
  [ "$status" -ne 0 ]
  grep -q '^TRUG_ORIGIN=http://localhost:8000$' "$HOME/.trug/.env"
}

@test "set-origin: refuses an IP, which WebAuthn will not accept as an RP ID" {
  run bash "$TRUG" set-origin https://192.168.1.5:8000
  [ "$status" -ne 0 ]
  assert_contains "WebAuthn won't accept an IP address" "$output"
}

@test "set-origin: refuses a URL with a path rather than silently trimming it" {
  run bash "$TRUG" set-origin https://example.com/trug
  [ "$status" -ne 0 ]
}

@test "set-origin: restarts and then runs the doctor, so a bad edit surfaces now" {
  run bash "$TRUG" set-origin https://trug.tail1234.ts.net
  [ "$status" -eq 0 ]
  grep -q -- "--project-directory $HOME/.trug up -d" "$DOCKER_LOG"
  grep -q 'trug-doctor' "$DOCKER_LOG"
}

@test "set-origin: warns that existing passkeys stop working" {
  run bash "$TRUG" set-origin https://trug.tail1234.ts.net
  assert_contains "create their passkey again" "$output"
}

@test "set-origin: a failing doctor is surfaced, not swallowed" {
  export DOCKER_STUB_DOCTOR_EXIT=2
  run bash "$TRUG" set-origin https://trug.tail1234.ts.net
  [ "$status" -ne 0 ]
}

@test "status: a stopped container is not reported as doctor warnings" {
  # `compose exec` exits 1 when the service is down — the same code the doctor
  # uses for warnings. Conflating them sends someone to re-read output that was
  # never printed.
  export DOCKER_STUB_RUNNING=no
  run bash "$TRUG" status
  [ "$status" -ne 0 ]
  assert_contains "isn't running" "$output"
  assert_contains "trug logs   to see why" "$output"
}

@test "set-origin: a container that didn't come back up isn't blamed on the doctor" {
  export DOCKER_STUB_RUNNING=no
  run bash "$TRUG" set-origin https://trug.tail1234.ts.net
  [ "$status" -ne 0 ]
  assert_contains "isn't running" "$output"
  refute_contains "doctor is not happy" "$output"
}

@test "set-origin: refuses a bracketed IPv6 literal instead of writing '[' as the RP ID" {
  run bash "$TRUG" set-origin "https://[::1]:8000"
  [ "$status" -ne 0 ]
  run grep '^TRUG_RP_ID=' "$HOME/.trug/.env"
  [ "$output" = "TRUG_RP_ID=localhost" ]
}

@test "set-origin: refuses credentials embedded in the URL" {
  run bash "$TRUG" set-origin "https://user@trug.example.com"
  [ "$status" -ne 0 ]
}

@test "set-origin: refuses a dotless host, which browsers reject as an RP ID" {
  run bash "$TRUG" set-origin "https://trugbox"
  [ "$status" -ne 0 ]
  assert_contains "no dot" "$output"
}

@test "set-origin: an unwritable .env fails loudly and changes nothing" {
  # Root ignores the permission bits, so this can only be tested as a normal
  # user — which is everyone running trug, but not a root container.
  [ "$(id -u)" -ne 0 ] || skip "running as root: file permissions are not enforced"
  chmod 500 "$HOME/.trug"
  run bash "$TRUG" set-origin https://trug.tail1234.ts.net
  chmod 700 "$HOME/.trug"
  [ "$status" -ne 0 ]
  grep -q '^TRUG_ORIGIN=http://localhost:8000$' "$HOME/.trug/.env"
}

@test "status: passes the doctor's exit code through" {
  export DOCKER_STUB_DOCTOR_EXIT=1
  run bash "$TRUG" status
  [ "$status" -eq 1 ]

  export DOCKER_STUB_DOCTOR_EXIT=2
  run bash "$TRUG" status
  [ "$status" -eq 2 ]
}
