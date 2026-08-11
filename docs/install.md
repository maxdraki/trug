# Installing Trug

Trug is one container and one SQLite file. Pick whichever of these suits the box you have.

- [One command](#one-command) — a Mac, a NAS, a Pi, anything with Docker on it
- [One click on Railway](#one-click-on-railway) — no terminal, ~$5/month
- [Docker Compose](#docker-compose) — the same box, by hand
- [A Raspberry Pi](#a-raspberry-pi) — with three Pi-shaped traps worth knowing
- [Your own fork on Railway](#your-own-fork-on-railway) — if you want to build your own

Whichever you choose, one rule decides whether it works: **passkeys need HTTPS or literal
`localhost`.** Getting other people onto your instance is [remote access](remote-access.md).

## One command

```sh
curl -fsSL https://raw.githubusercontent.com/maxdraki/trug/main/install.sh | sh
```

Docker needs to be there first, with Compose v2 and `curl`. The installer checks for all three and
names the fix if one is missing. It won't put a container runtime on your machine — if it can't
find one it lists your options (Docker Desktop, OrbStack, Colima, or Docker Engine on Linux) and
stops. If Docker Desktop is installed but not running, it'll start it for you.

It does the rest itself: writes `~/.trug`, generates all four tokens up front, pulls the image,
starts it, waits until the server actually answers rather than trusting `up -d`, and drops a
`trug` command into `~/.local/bin`.

A first install finishes by printing your bootstrap token. Open `http://localhost:8000`, choose
**"use an access token"**, paste it, pick a name, create your passkey. Then `trug share` to get
everyone else on it.

The container publishes on every interface, on every platform. That's deliberate — the point of a
household list is the other phone in the house, and it's what makes `trug share` work at all.

### The trug command

| Command | |
| --- | --- |
| `trug up` | start it |
| `trug down` | stop it |
| `trug status` | is it healthy, and is anything misconfigured |
| `trug logs [-f]` | what the server is saying |
| `trug share` | a link and a QR for everyone else on the network |
| `trug set-origin <https-url>` | point Trug at its new name, and check it took |
| `trug doctor [args…]` | run `trug-doctor` in the container, flags and all |

That's the whole CLI — seven verbs, no more. `trug status` and `trug set-origin` both run
`trug-doctor` and hand you its exit code: `0` healthy, `1` warnings, `2` broken.

Every one of them works from any directory: the compose file is in `~/.trug` and `trug` knows it.
That is the difference between these and the `docker compose …` lines further down, which need a
`docker-compose.yml` in the directory you're standing in. Where no verb covers what you want, name
the directory:

```sh
docker compose --project-directory ~/.trug pull
```

`trug doctor` exists so recovery isn't a Docker exercise. `trug doctor recover --invite NAME`
mints an invite link when nobody can sign in — more in [recovery](operations.md#recovery).

`trug set-origin` is the one that saves you a silent afternoon. It writes `TRUG_ORIGIN`, derives
`TRUG_RP_ID` as the bare host, restarts, and checks the result. It also refuses the four inputs
that break passkeys with no error anywhere — see
[tell Trug its new name](remote-access.md#tell-trug-its-new-name).

If `trug` isn't found afterwards, open a new terminal: the installer adds `~/.local/bin` to your
`PATH` in `.zshrc` / `.bashrc`, and your current shell doesn't know about it yet.

### Re-running it, and the overrides

Run the same command again. Re-running is the repair path, not a reinstall: it never regenerates a
live token, never touches `data/`, and leaves your `.env` edits alone. It rewrites the compose
file and starts anything that isn't running.

On an instance you've already claimed it won't reprint the bootstrap token. That token is inert
once the first account exists, so printing it would only send you to a gate that refuses it.

Knobs, if you need them:

| Variable | |
| --- | --- |
| `TRUG_HOME` | where to install (default `~/.trug`) |
| `TRUG_PORT` | host port (default `8000`) |
| `TRUG_REF` | the git ref to fetch the `trug` command from (default `main`) |
| `TRUG_IMAGE` | the image to run (default `ghcr.io/maxdraki/trug:latest`) — for one you built or mirrored yourself |
| `TRUG_HEALTH_TIMEOUT` | seconds to wait for the first healthy answer (default `90`) |
| `NO_COLOR` | plain text, no escape codes |

```sh
curl -fsSL https://raw.githubusercontent.com/maxdraki/trug/main/install.sh | TRUG_PORT=8080 sh
```

Something already on port `8000` is the usual reason the health wait times out.

On a 32-bit Raspberry Pi OS the installer stops before it pulls anything and tells you to reflash
— see [the Pi section](#you-need-the-64-bit-os).

## One click on Railway

[**Deploy on Railway**](https://railway.com/deploy/Y944p3?referralCode=sXZPkF&utm_medium=integration&utm_source=template&utm_campaign=generic)
builds Trug, attaches the volume, generates your tokens and works out the passkey settings from
your new domain. There is nothing to type and no logs to read.

1. Click the button and deploy. Railway gives the service a domain like
   `trug-production.up.railway.app`.
2. Open that URL. Trug will tell you it hasn't been claimed yet and ask for a bootstrap token.
3. Copy `TRUG_BOOTSTRAP_TOKEN` from the service's **Variables** tab and paste it in.
4. Pick a name, create your passkey, then invite everyone else from **Settings → Members**.

Costs Railway's **Hobby plan (~$5/month, which includes $5 of usage)**. Trug is a single small
container and sits comfortably inside that allowance.

All three tokens are generated once at deploy time and pinned from birth, so they live in the
Variables tab and survive restarts. The "read the tokens from the logs" business below applies
only to the manual paths.

## Docker Compose

The long way round, if you'd rather see every step — or you're working from a clone anyway. From
a clone to your household on their phones.

Every `docker compose …` line in this section runs **inside the clone**, which is where the
compose file is. Run one somewhere else and Docker says `no configuration file provided: not
found`. There's no `trug` command on this path — that comes with
[the installer](#one-command), which is a separate install with its own database, not a wrapper
you can bolt onto a clone.

### 1. Clone and start it

```sh
git clone https://github.com/maxdraki/trug.git
cd trug
docker compose pull      # skip this and you build from source instead — see below
docker compose up -d
```

That starts a single container serving everything on port `8000`. The container fixes the
ownership of the `./data` volume itself on start-up — it runs as a pinned non-root user and
`chown`s the mount via `gosu` before dropping privileges — so there is **no manual `chown` step**.

> **Docker Desktop users:** clone under your home directory, or add the clone's path to Docker's
> Settings → Resources → File sharing. Otherwise the `./data` bind mount is denied. Native Docker
> Engine and a Raspberry Pi are unaffected.

### 2. Read the generated tokens from the logs

With nothing pinned, Trug generates a one-time bootstrap token plus two machine tokens and prints
them on boot:

```sh
docker compose logs | grep -E 'TRUG_BOOTSTRAP_TOKEN|TRUG_TOKEN'
```

| Token | Role |
| --- | --- |
| `TRUG_BOOTSTRAP_TOKEN` | **First-user claim.** Use it once to create the very first account. It stops working the moment that account is claimed, and prints only while no account exists. |
| `TRUG_TOKEN_RING` | **Machine key** for the voice ring / webhook (`/api/capture`). |
| `TRUG_TOKEN_MCP` | **Machine key** for token-based MCP and automation clients. |

There is no household roster to configure — no `TRUG_USERS`, no per-user tokens. The first person
claims the instance, then invites everyone else by name from inside the app.

**Every unpinned token regenerates on each restart**, and the banner reprints on every boot while
any token is still generated. So pin the machine keys before you rely on them.

### 3. Set your domain, pin the machine tokens, restart once

```sh
cp .env.example .env
```

```sh
# Required for passkeys on a real domain — WebAuthn binds credentials to these:
TRUG_RP_ID=trug.example.com          # bare host, no scheme, no port
TRUG_ORIGIN=https://trug.example.com # full origin people actually visit

# Pin the machine keys so a restart doesn't log out the ring / MCP clients
TRUG_TOKEN_RING=…
TRUG_TOKEN_MCP=…
```

Then `docker compose up -d` to pick them up. Every other setting is in
[configuration](configuration.md).

**Just trying it on your laptop?** The defaults (`TRUG_RP_ID=localhost`,
`TRUG_ORIGIN=http://localhost:8000`) already satisfy WebAuthn, so passkeys work on `localhost`
with no config at all. You only need these two once you serve Trug on a real domain, where they
must match the address in the browser or every passkey ceremony fails.

If you didn't pin the bootstrap token, re-read it from the logs after this restart — the
pre-restart value is now dead.

### 4. Check the configuration

Before you open the app, run the built-in doctor. It catches the silent killers — a `TRUG_RP_ID`
or `TRUG_ORIGIN` that doesn't match the URL people load breaks every passkey ceremony with no
error anywhere — and prints the current tokens, so you never have to grep the logs again:

```sh
docker compose exec trug trug-doctor
```

It exits `0` healthy, `1` on warnings, `2` on errors, and every non-OK line says exactly what to
change. Add `--json` for a machine-readable report an agent can apply, or `--redact` to hide token
values before pasting the output into an issue. Fix anything it flags before claiming your account.

### 5. Claim the first account

Open the `TRUG_ORIGIN` you set — on your laptop with the defaults, `http://localhost:8000` — and
on the sign-in gate choose **"use an access token"**. Paste the bootstrap token once. Because no
account exists yet, Trug switches to **"create the first account"**: type your name and create a
passkey. You're in, and the bootstrap token is spent.

> **This step needs HTTPS or literal `localhost`.** A browser only creates a passkey in a secure
> context, and `TRUG_RP_ID` can't be an IP — so `http://192.168.x.x:8000` or `http://pi.local:8000`
> serves the list fine but **cannot enrol a passkey**. See [remote access](remote-access.md), and
> the Pi section below for the trick that avoids the problem entirely.

### 6. Invite your household

Signed in, open **Settings → Members → "+ invite someone"**, type any new name, and share the
generated link. It's single-use and expires in 24 hours. They open it on their phone, create a
passkey, and they're in. Any enrolled member can invite others or remove members — it's a flat
household, no admin tier.

Afterwards, **Settings** is where you manage the household: revoke any signed-in device's session,
or remove any member — except the last one, so the instance can't be orphaned. If someone lands on
a browser with no passkey support, the gate's "use an access token" link is always there.

Passkeys and the roster live in the database and survive restarts regardless. Pinning tokens only
keeps the *machine-token* values stable.

That's a working shared list, with no LLM key required.

## A Raspberry Pi

A Pi makes a good Trug box: it doesn't sleep and take the list with it, and one container plus one
SQLite file is well within a Pi's means. `ssh` in and run [the one-liner](#one-command), with
these Pi-shaped things to know.

### Getting people on it

Headless isn't a problem here. `trug share` hands out a link that needs no passkey, so the whole
house is on the list before anyone has an account. See [remote access](remote-access.md).

When you want real accounts, the Pi needs an HTTPS name — Tailscale Serve, or your own domain with
Caddy — then `trug set-origin https://…` on the Pi.

#### The SSH tunnel

If you want an account on the Pi before it has a name, there's still the tunnel:
`ssh -L 8000:localhost:8000 pi@raspberrypi.local`, then open `http://localhost:8000` on your own
machine. The browser sees `localhost`, so Touch ID enrols against the Pi with no TLS anywhere. It
gets *you* an account; phones still need the name.

### You need the 64-bit OS

The published image is `linux/amd64` and `linux/arm64` only. Plenty of 64-bit Pis are running a
32-bit Raspberry Pi OS, where `uname -m` reports `armv7l` and Docker fails with `no matching
manifest for linux/arm/v7`. Check first:

```sh
uname -m        # want aarch64; armv7l means you're on the 32-bit OS
```

If it's 32-bit, reflash with the 64-bit Raspberry Pi OS. Building from source on a 32-bit Pi is
not a shortcut worth taking.

### Docker's group membership needs a logout

After `sudo apt install docker.io docker-compose-plugin`, `docker ps` fails with a permission
error until your user is in the `docker` group:

```sh
sudo usermod -aG docker $USER
```

That takes effect on your **next login**, not immediately. Log out and back in — or `exit` and
`ssh` in again — before you try Docker. This trips up nearly everyone.

Also make sure Docker itself starts on boot, or `restart: unless-stopped` won't bring Trug back
after a power cut:

```sh
sudo systemctl enable docker
```

### Put the database somewhere better than the SD card

SQLite writes constantly, and SD cards wear out and corrupt. If the Pi has a USB SSD, put the
install on it — `TRUG_HOME=/mnt/ssd/trug` on the installer one-liner, or clone there if you're
doing it by hand. The `trug` command reads the same variable, so keep it exported (or in your
`.bashrc`) and every verb keeps finding the install. If there's no SSD, take
[backups](operations.md#backup) on a schedule and mean it.

## Your own fork on Railway

If you'd rather build from your own fork, Trug runs on [Railway](https://railway.app) as a single
service with one volume.

1. **Deploy from the repo.** New Project → Deploy from GitHub repo → pick your fork. Railway builds
   the `Dockerfile` as-is.
2. **Add a volume mounted at `/data`.** This is where `trug.db` lives; without it the list resets on
   every deploy. The container `chown`s the volume on boot, so Railway's root-owned mount is handled
   for you.
3. **Set the environment variables.** `TRUG_RP_ID` and `TRUG_ORIGIN` for your domain. Optionally pin
   `TRUG_TOKEN_RING` / `TRUG_TOKEN_MCP` and `TRUG_BOOTSTRAP_TOKEN`, and set the `LLM_*` keys. Behind
   Railway's proxy, also set `TRUG_TRUSTED_PROXY_HOPS=1` so the rate limiter keys on real client IPs
   rather than the shared proxy address. Leave `PORT` alone: Railway sets it and points your domain
   at the same port, and Trug binds whatever it's given. Only pin `PORT` if you have also pinned the
   domain's target port — the two must match, or the domain answers 502 while the container looks
   perfectly healthy.
4. **Use the generated domain.** Under Settings → Networking, generate a domain. `TRUG_ORIGIN` must
   exactly match it (`https://trug.up.railway.app`) and `TRUG_RP_ID` must be the bare host
   (`trug.up.railway.app`), or passkeys and the MCP OAuth flow won't line up with the URL people
   actually load.

Any token you don't pin is generated and printed in the deploy logs, on first boot and again on
every restart while it's unpinned. Grab the bootstrap token from the logs, claim the first account,
and invite your household from Settings → Members.

```sh
railway logs | grep -E 'TRUG_BOOTSTRAP_TOKEN|TRUG_TOKEN'
```

## Two things about the image

Both of these are about the **repo's** compose file. The installer writes its own into `~/.trug`
with neither trap in it, so skip this section if you used the one-liner.

**Prebuilt or from source.** On a tagged release, a multi-arch (`amd64` + `arm64`) image publishes
to GHCR at `ghcr.io/maxdraki/trug`. The repo's compose file carries
`image: ghcr.io/maxdraki/trug:latest` alongside `build: .` — and because that `build:` section is
present, `docker compose up` **builds from source by default**, which is a multi-minute wait on a
Pi. Run `docker compose pull` first (or `docker compose up --pull always`) for the ~30 second pull
instead. If the image isn't there, Compose builds from source as usual, and `docker compose build`
always forces that. The installer's compose file has no `build:` at all — it only ever pulls.

**Compose version.** The `env_file: [{ path, required }]` long form in the repo's
`docker-compose.yml` needs Compose **v2.24+** (January 2024). A box set up with Debian's
`docker.io` plus a standalone `docker-compose` v1 will hard-error on that key — install Docker's
own apt repo for a current Compose. The installer writes the short `env_file: .env` form, which
older Compose takes.

## Next

- [Get your household on it](remote-access.md)
- [Connect Claude, or the ring](integrations.md)
- [Backups, upgrades, and the doctor](operations.md)
- [Something's wrong](troubleshooting.md)
- [Running Trug on a hosting platform](hosting.md) — if you're the one offering it to other people
