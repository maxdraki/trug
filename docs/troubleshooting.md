# When something's wrong

Start here:

```sh
trug status                             # if you used the installer
docker compose exec trug trug-doctor    # if you didn't
```

It catches most of what follows and tells you what to change. Exit `0` is healthy, `1` is
warnings, `2` is broken. If it's clean and you're still stuck, find your symptom below.

## Sign-in and passkeys

| Symptom | Cause | Fix |
| --- | --- | --- |
| Passkeys fail on a LAN address (`http://<ip>` or `.local`) | A browser won't create a passkey outside a secure context, and `TRUG_RP_ID` can't be an IP. HTTPS or literal `localhost` is a prerequisite for creating accounts, not a nice-to-have. | Give the box a trusted HTTPS name — see [remote access](remote-access.md) — then `trug set-origin https://…`. On a headless box you can also claim the first account through an SSH tunnel: [the Pi section](install.md#the-ssh-tunnel). |
| The gate says "this address can't create an account" | Exactly what it says: a plain-`http` address, so the browser won't make a passkey. Not the browser's fault and not a bug. | Use the list from there anyway — `trug share` prints the link that works. The "Not Secure" chip alongside it is expected on a home-network address. For real accounts, give the box an HTTPS name and run `trug set-origin`. |
| The gate says "passkeys can't be made against an IP address" | You're on the machine Trug runs on, but you loaded it by IP (`http://127.0.0.1:8000`). Loopback *is* a secure context, so the ceremony would start and then die on the RP ID. | Open `http://localhost:8000` instead — same Trug, same port. If `TRUG_ORIGIN` is the IP too, `trug-doctor`'s `origin.host_is_ip` check hands you the exact two settings. |
| A brand-new instance shows the address message instead of "this trug hasn't been claimed yet" | It's unclaimed *and* opened somewhere a passkey can't be made. The address is the blocking problem, so it gets said first. | Claim it from `http://localhost:8000` on the machine itself, or over an SSH tunnel. The pasted token still works there — the field just isn't led with. |
| The gate says "this browser doesn't support passkeys" | This one really is the browser. | Choose "use an access token" instead — it's always there. Or open Trug on a device with Face ID, Touch ID, or a security key. |
| The gate says an address "needs a name", on an `https://` LAN address | A proxy or certificate in front of a LAN IP. The connection is fine; the IP is the problem, and WebAuthn won't take one as an RP ID. | Give the box a name — `tailscale serve --bg 8000` is the quickest — then `trug set-origin` with the name it prints. `localhost` is no help here: on the phone, that's the phone. |
| Passkey create or sign-in always fails, no error anywhere | `TRUG_RP_ID` / `TRUG_ORIGIN` don't match the URL in the browser | Set `TRUG_RP_ID` to the bare host and `TRUG_ORIGIN` to the exact origin — scheme, host, and port if it isn't standard. Restart. On Railway they must equal the generated domain. |
| Everyone's passkeys stopped working at once | You changed `TRUG_RP_ID` | Credentials are bound to the hostname they were created under. Everyone re-enrols once against the new name. |
| Bootstrap token gets a 403 | The instance is already claimed — bootstrap is one-time and first-user-only | Sign in with your passkey, or have an enrolled member send you an invite. If every device is lost: `trug-doctor recover --reset-bootstrap` re-opens the claim without deleting anything. |
| Invite link says "invalid or expired" | Single-use, 24-hour TTL — already used, or old | Mint a fresh one: Settings → Members → "+ invite someone". |
| Locked out entirely | — | Host access is the recovery credential. See [recovery](operations.md#recovery). |

## Install and start-up

| Symptom | Cause | Fix |
| --- | --- | --- |
| `no matching manifest for linux/arm/v7` | The image is 64-bit only and you're on a 32-bit OS | Check with `uname -m`. `armv7l` means reflash with the 64-bit Raspberry Pi OS. |
| `permission denied` talking to the Docker daemon | Your user isn't in the `docker` group | `sudo usermod -aG docker $USER`, then log out and back in. The group change doesn't apply to your current session. |
| Compose errors on the `env_file` key | The `{ path, required }` long form needs Compose v2.24+ | Install Docker's own apt repo for a current Compose. Debian's `docker.io` plus standalone `docker-compose` v1 won't do. |
| `docker compose up` takes several minutes | `build: .` is present, so it builds from source by default | `docker compose pull` first, or `up --pull always`. |
| The `./data` bind mount is denied on Docker Desktop | The clone is outside a shared path | Clone under your home directory, or add the path in Settings → Resources → File sharing. |
| Container can't write the database | Rare — the entrypoint chowns the volume on boot | Confirm you haven't overridden the entrypoint, and that `./data` isn't mounted read-only. No manual `chown` should be needed. |
| Trug doesn't come back after a reboot | Docker's own service isn't enabled | `sudo systemctl enable docker`. `restart: unless-stopped` can't help if Docker never starts. |
| The domain answers 502 but the container looks healthy | A pinned `PORT` doesn't match the domain's target port | On Railway, leave `PORT` alone unless you've also pinned the target port. |

## The `trug` command

| Symptom | Cause | Fix |
| --- | --- | --- |
| `trug: command not found`, right after installing | `~/.local/bin` isn't on this shell's `PATH` yet | Open a new terminal. The installer appends the `export` line to `.zshrc` / `.bashrc`, and your current shell started before that. For right now: `export PATH="$HOME/.local/bin:$PATH"`. |
| `No trug install found at ~/.trug` | The command is on your PATH but the install directory isn't there — a different `TRUG_HOME`, or a Compose install the installer never touched | Point it at the real one with `TRUG_HOME=/path/to/it trug status`, or run the installer, which is safe to re-run. |
| The installer says the downloaded `trug` command "doesn't look like a script" | A captive portal answered the download instead of GitHub | Trug itself is already running — only the wrapper is missing. Get onto the network properly and run the installer again. |
| `trug status` exits `1` or `2` and you were expecting `0` | It passes `trug-doctor`'s exit code straight through | `1` is warnings, `2` is errors. Read the lines above the exit — each one names what to change. |
| `trug set-origin` refuses your URL | It validates rather than guessing. A bare hostname, a plain-`http` origin, a trailing path, or an IP address are all rejected. | Paste the origin exactly as the address bar shows it: scheme, host, port if it's unusual, nothing after. An IP can't be fixed by any setting — the box needs a name. |
| The `trug share` link works on the machine but a phone times out | Usually the address, not a firewall — and on Linux with `ufw` it is almost certainly not ufw, which doesn't filter Docker-published ports at all. | Compare what `trug share` printed against `ip -4 addr show`: it should be the `192.168.x` / `10.x` address your phone is on, not a VPN or Docker-bridge address. Then check `docker compose --project-directory ~/.trug ps` really shows the port published on `0.0.0.0`. Then rule out a guest SSID or AP client isolation by trying a second device. |
| You're on Linux and want the port *actually* closed to the network | `ufw` won't do it. Docker forwards published ports past ufw's rules entirely — a documented Docker behaviour, not a bug — so a default-deny policy is not protecting this. | Write the rule in the `DOCKER-USER` chain, or don't publish on all interfaces: change the compose `ports:` entry to `"127.0.0.1:8000:8000"` and reach it over Tailscale or an SSH tunnel instead. |
| `trug set-origin` succeeded but a phone still behaves as before | The old LAN link and the new HTTPS name are separate browser origins with separate local storage, so that phone still holds its share token | Send everyone the new URL and have them open it there. It's a one-time re-point. |

## Tokens and clients

| Symptom | Cause | Fix |
| --- | --- | --- |
| Ring or MCP client stopped working after a restart | Generated tokens rotate on every restart until pinned | Pin `TRUG_TOKEN_RING` / `TRUG_TOKEN_MCP` in `.env` to the values from the banner. |
| No token banner in the logs | Every token is already pinned, so nothing was generated to print | Read them from `.env`, or Settings → Connections once you're on a passkey session. |
| `/mcp` returns 401 in a connector | Expected before OAuth — it's advertising the auth flow | Complete the connector sign-in, or for token clients send `Authorization: Bearer <MCP token>`. |
| Settings → Members or Connections returns 401 | You're on the token fallback, which is a machine principal | Those screens need a real passkey session. See [about the token fallback](remote-access.md#about-the-token-fallback). |

## The app itself

| Symptom | Cause | Fix |
| --- | --- | --- |
| Old UI after an update | PWA service-worker cache | Hard-reload, or remove the installed PWA and reinstall. On mobile, close all tabs first. |
| A phone on wifi can't install to the home screen | Service workers need a secure context | Expected on a plain-HTTP LAN address. The same goes for cold-starting offline and for push. [Give it an HTTPS name](remote-access.md#rung-two-a-real-https-name). |
| Items are attributed to `mcp` in the database | They were added through a share link or an assistant | That's the shared machine principal, not a person. Nothing in the app renders it, so nobody sees an "added by mcp" label. [How the share link works](remote-access.md#rung-one-trug-share). |
| The rate limiter treats everyone as one client | `TRUG_TRUSTED_PROXY_HOPS` is still `0` behind a proxy | Set it to the number of trusted hops. See [configuration](configuration.md#behind-a-proxy). |

## Still stuck

Run the doctor with `--redact`, which hides token values, and include the output when you
[open an issue](https://github.com/maxdraki/trug/issues):

```sh
docker compose exec trug trug-doctor --redact
```
