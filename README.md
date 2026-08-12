<p align="center">
  <img src="icon/trug-logo.svg" alt="Trug logo — a basket with a check mark" width="112">
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="icon/trug-wordmark-dark.svg">
    <img src="icon/trug-wordmark.svg" alt="Trug" width="150">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/maxdraki/trug/actions/workflows/ci.yml"><img src="https://github.com/maxdraki/trug/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI status"></a>
  <a href="https://github.com/maxdraki/trug/releases/latest"><img src="https://img.shields.io/github/v/release/maxdraki/trug?color=a6e3a1&label=release" alt="Latest release"></a>
  <a href="https://github.com/maxdraki/trug/pkgs/container/trug"><img src="https://img.shields.io/badge/ghcr.io-multi--arch%20image-89b4fa?logo=docker&logoColor=white" alt="Container image on GHCR"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/maxdraki/trug?color=cba6f7" alt="MIT licence"></a>
</p>

A self-hosted shared shopping list for one household. A phone each, an installable PWA, and a
single SQLite file behind a FastAPI server — no cloud account, no subscription, no third-party
anything.

It's fast where it counts: an optimistic, offline-first list you can read and tick off in a shop
with no signal, passkey sign-in so there are no passwords to type at the door of a store, a
voice-ring capture endpoint that drops "milk, eggs and bread" straight onto the list, and an MCP
server so Claude can read and edit the list for you. No LLM key needed — groceries arrive iconed
and sorted into aisles out of the box.

<p align="center">
  <img src="docs/img/trug-demo.gif" alt="Claude reads a recipe and adds the ingredients to Trug over MCP; the list fills up live, grouped into aisles" width="820">
</p>

<p align="center"><em>Ask Claude to stock the list — it fills in live, iconed and sorted into aisles.</em></p>

<p align="center">
  <img src="docs/img/readme-list-mocha.png" alt="Trug — shopping list grouped into aisles, Mocha theme" width="300">
  &nbsp;&nbsp;
  <img src="docs/img/readme-list-latte.png" alt="Trug — the same list in the Latte (light) theme" width="300">
</p>

## Install

### One command

```sh
curl -fsSL https://raw.githubusercontent.com/maxdraki/trug/main/install.sh | sh
```

Needs Docker, Compose v2 and `curl` already installed — it checks for all three and names the fix
if one is missing, but it won't put a container runtime on your machine behind your back. It
handles the rest: `~/.trug`, all four tokens, the image, the container, and a `trug` command —
`up`, `down`, `status`, `logs`, `share`, `set-origin`, `doctor`. Those work from any directory,
which the raw `docker compose` lines below do not. Re-running the installer is the repair path; it
never touches your data or a live token.

On a first install it prints your bootstrap token at the end. Open `http://localhost:8000`, choose
"use an access token", paste it, create your passkey. Then `trug share` for a link and a QR the
rest of the house can open.

→ [details, and the Raspberry Pi notes](docs/install.md#one-command)

### One click, no terminal

<p align="center">
  <a href="https://railway.com/deploy/Y944p3?referralCode=sXZPkF&utm_medium=integration&utm_source=template&utm_campaign=generic"><img src="https://railway.com/button.svg" alt="Deploy on Railway" height="32"></a>
</p>

Railway builds Trug, attaches the volume, generates your tokens and works out the passkey settings
from your new domain. Open the URL it gives you, paste `TRUG_BOOTSTRAP_TOKEN` from the Variables
tab, pick a name, create a passkey. Runs on the Hobby plan, about $5/month.

→ [details](docs/install.md#one-click-on-railway)

### Docker, by hand

A Mac, a NAS, a Raspberry Pi. One container, one SQLite file.

```sh
git clone https://github.com/maxdraki/trug.git
cd trug
docker compose pull && docker compose up -d
docker compose logs | grep TRUG_BOOTSTRAP_TOKEN
```

Open `http://localhost:8000`, choose **"use an access token"**, paste that token, and create your
passkey. Then invite the household from Settings → Members.

The `cd` is load-bearing. Compose commands need a `docker-compose.yml` in the directory you're
standing in, so run these in the clone or you'll get `no configuration file provided: not found`.

Pull before you bring it up — there's a `build: .` in the compose file, so a plain `up` compiles
from source instead of taking the 30-second image.

→ [full walkthrough, including a Raspberry Pi](docs/install.md)

### Getting everyone else on it

**Passkeys need HTTPS or literal `localhost`.** A phone at `http://192.168.1.5:8000` will load the
list and then refuse to create an account, because browsers won't make a passkey in an insecure
context. `trug share` is the no-setup way round it for the same wifi; a real HTTPS name plus
`trug set-origin` is how you do it properly.

→ [remote access](docs/remote-access.md)

## What it does

Everything lives on one screen, grouped into store-order aisles. Settings tuck away into
collapsible sections — theme (Mocha, Latte, and the rest of Catppuccin), accent colour, your
signed-in devices, invite links, and the connection details for AI assistants and the ring.

Your whole instance is one SQLite file, so a backup is one command and a restore is three.

<p align="center">
  <img src="docs/img/settings-collapsible.png" alt="Trug settings — collapsible sections for theme, accent, density, devices, connections, AI enrichment and members" width="320">
</p>

## Philosophy

Trug does one thing — make Saturday's shop faster for one household — and deliberately refuses
everything else. The non-goals, which are the deliberate event horizon:

- **No recipes, meal planning, or pantry inventory.** Those are a different product.
- **No passwords and no admin tiers.** A flat, invite-based passkey roster — no registration
  forms, no password resets, no owner/admin hierarchy.
- **No multi-tenancy.** One household per instance; nothing household-specific is baked into the
  code.
- **No native apps or Play Store.** Installable PWA only.
- **No price tracking, budgets, or store loyalty anything.**

Any feature request gets tested against: *"does this make Saturday's shop faster?"* If no →
parking lot.

## Docs

| | |
| --- | --- |
| [Install](docs/install.md) | The one-liner, Railway, Docker Compose, a Raspberry Pi, your own fork |
| [Remote access](docs/remote-access.md) | Getting phones on it — `trug share`, Tailscale, your own domain, tunnels |
| [Configuration](docs/configuration.md) | Every setting, and `config.yaml` |
| [Running it](docs/operations.md) | Backups, restores, upgrades, the doctor, recovery |
| [Integrations](docs/integrations.md) | MCP for Claude, the Pebble ring, bring-your-own LLM key |
| [Hosting](docs/hosting.md) | For platform operators — the image, the volume, the UID, the one fiddly setting |
| [Troubleshooting](docs/troubleshooting.md) | Symptoms and fixes |

**Deploying it with Claude Code.** This repo ships a skill that walks the whole self-host through
for you. Clone it, open Claude Code, and say *"deploy trug"* — the
[`deploy-trug`](.claude/skills/deploy-trug/SKILL.md) skill collects your domain, brings the stack
up with Compose or Railway, verifies the first boot, and mints the passkey invites.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup — `pytest` in `server/`,
`vitest` in `web/`, and `docker compose up` for the lot.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Trug contributors.

## Credits

Trug is themed with [Catppuccin](https://github.com/catppuccin/palette), and bundles
[Tabler Icons](https://tabler.io/icons) and the [Inter](https://github.com/rsms/inter) and
[Space Grotesk](https://github.com/floriankarsten/space-grotesk) typefaces. Full license texts for
the redistributed assets are in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
