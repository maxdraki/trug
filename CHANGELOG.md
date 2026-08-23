# Changelog

All notable changes to Trug are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.1] - 2026-08-23

### Fixed

- **Every way of ticking something off can be undone.** Swiping right offered you an undo;
  tapping the row — the way most things get ticked off — offered nothing, and getting a mistap
  back meant opening the basket drawer, which starts closed, and finding the item in it. Both
  gestures now put the same five-second Undo at the bottom of the screen, and it names the item,
  so ticking off three things in a row no longer leaves you pressing Undo on whichever one it
  happened to mean.
- **An undo notice can no longer quietly take the place of a more important one.** There was one
  notice at the bottom of the screen and two things using it. Clear the basket and then tick one
  more thing off, and the offer to put twelve items back was replaced by one about the thing you
  just ticked — while the twelve counted down to nothing behind it, unseen. Now there is a single
  notice, so anything it replaces is replaced in front of you, and ticking something off will not
  displace an offer to undo a delete or a cleared basket: those are the two you cannot get back
  another way, while a ticked-off item is one tap away in the basket.
- **Undo does not do the opposite of undo.** Tick something off, then put it back yourself — or
  let someone else's phone put it back — and the notice used to stay, with a button that would
  now tick it off again. It steps aside instead.

- **Swiping an item into the basket now finishes the way swiping it away does.** It used to snap
  back under your thumb the instant you let go, then play a separate little animation — so the
  gesture that worked looked jerkier than the one that didn't quite. Now the row carries on in the
  direction you threw it, exactly like a delete. Which way you swipe, the colour behind the row and
  the icon still tell you which one you did.

- **Remove offers you an undo now, wherever you reach for it.** Deleting an item also stops Trug
  offering that name as a shortcut, and the only thing that brings it back is putting the item
  straight back — so the Remove button inside an item now gets the same five-second Undo that
  swiping has always had. Before, a mistap there quietly took the name out of the frequently-added
  chips and out of typeahead for good, along with however many shops of history it had behind it.

## [0.4.0] - 2026-08-15

### Added

- **Say it instead of typing it.** A mic sits in the add bar, and what it hears goes onto the list
  by the same route a typed item does. It appears only where the browser can actually hear: Chrome
  and Edge yes, Firefox only with `media.webspeech.recognition.enable` set, and — for now — not in
  an installed web app on iOS, where the API is present but dead. Rather than sniff for that, the
  button asks once and retires itself on that device if the answer is no, so it will light up on
  its own the day Apple ships support. Note that recognition is the browser's, not Trug's: outside
  Chrome's on-device mode the audio goes to the browser vendor.
- **A buzz when something lands from elsewhere.** An item arriving from the smart ring or from an
  assistant over MCP gives the phone a short tick, so a change made in the kitchen is felt by
  whoever is holding the list in the shop. Items you add yourself never buzz. There is a switch in
  Settings under Alerts, and the browser has the final say — Safari and Firefox cannot vibrate at
  all, and no browser will until the page has been touched once.

## [0.3.2] - 2026-08-14

### Added

- **You can forget a shortcut.** "Frequently added" only ever grew, so a mis-heard voice capture —
  "Marty Rice" for basmati rice, "Papa Dums" for poppadoms — was offered forever with no way to
  stop it. Hold a shortcut on a phone, right-click it on a desktop, or press Delete on a focused
  one, and it goes, with five seconds to undo. Nothing is sent to the server until that window
  closes, so undo cannot fail.

### Changed

- **Deleting an item now removes its shortcut too.** It was the item you deleted; you should not
  keep being offered it. Undo is safe: a deleted shortcut is kept aside for an hour, so re-adding
  the name restores its count, its icon and its aisle rather than starting from scratch — a stray
  swipe on something you buy weekly no longer quietly drops it out of your shortcuts.
- **MCP's `remove_item` says what it now does** — it deletes the item *and* forgets that name's
  history, with no undo — and points at `check_item` for "we bought it". An assistant asked to tidy
  a list could otherwise erase the staples your shortcuts are built on.

### Fixed

- **British names.** "Loo paper" resolved to nothing and "bog roll" was filed in Bakery, because
  "roll" is a shorter word than the thing it was inside. Every name for the same product now lands
  on one icon and one aisle, and the sweep found a lot of neighbours in the wrong place: cling film
  and plastic wrap in Bakery, washing-up liquid reaching for the hand soap, sponge cake in Household
  (via the washing-up sponge), pepperoni and limescale remover and garlic bread and mango chutney
  all in Fruit & Veg, toilet duck in Meat & Fish, hand cream and nappy cream in Dairy. Poppadoms had
  no spelling that resolved at all; nor did chapati, roti or paratha.
- **"Nappies" and "ice lollies"** — the only forms anyone writes — resolved to nothing, because the
  lookup matched a trailing "s" but not "-y" to "-ies".
- **A shortcut with a slash in its name could never be forgotten.** A capture like "salt / pepper"
  kept its punctuation, and the key travelled in the URL path, which the server splits on slashes —
  so every attempt failed. On precisely the kind of mis-transcription the gesture exists to remove.
- **Forgetting something and then pocketing your phone lost the change.** The request waits out the
  undo window, and a suspended page never sent it: no error, and the shortcut back next Saturday.
- **A failure after you started typing said nothing at all**, because the notice lived in the
  shortcut tray and typing hides the tray.

## [0.3.1] - 2026-08-12

### Added

- **A Medicines aisle**, between Household and Pet — the far end of the non-food run, which is
  where the pharmacy counter actually is. Nasal spray had been landing next to the bin bags.

### Fixed

- **Creating a passkey could hang the screen forever.** If the ceremony never answered, the button
  said "creating…" until you reloaded — no timeout, no error, no retry. It is the first screen a
  new install shows, so it read as software that doesn't work. A ceremony is now bounded (using the
  timeout the server already advertises, floored and given grace so a real one is never cut short),
  and a device that doesn't answer gets a message and the button back, with what you typed intact.
  A prompt you dismiss yourself still says so — the two are deliberately different.
- **Four items were filed under the wrong aisle**, all through the same longest-match rule: "sun
  cream" and "antiseptic cream" resolved to Dairy & Eggs through the five-letter "cream", "cough
  sweets" to Cupboard through "sweets", and "vitamin water" to Household. There is now a test that
  no built-in name's singular or plural is another name in a different aisle.
- **An item with no icon showed its first letter in a colour unrelated to your accent**, which read
  as a bug rather than a fallback — and measured 2.43:1 in the light theme, so it was hard to read
  as well as out of place. The letter now sits on an accent-tinted chip like every other row, at
  5.58:1 or better across every theme and accent.

### Changed

- README screenshots reshot for the new UI: edge-to-edge list, shelf-label counts, text pills, the
  basket drawer and the Density setting. The demo GIF still shows the older list and stays for now.

## [0.3.0] - 2026-08-12

### Security

- **A shared database connection could authenticate you as another member of your household.**
  Each repository used one SQLite connection behind a lock that only writers took, and a
  connection caches prepared statements by SQL text — so two threads running the same query at
  once shared one statement, and one rebound it while the other was reading. Measured over 60,000
  concurrent session lookups on a WAL database: 846 valid sessions reported invalid, 448 crashes
  or garbled rows, and 583 that returned a complete, coherent row belonging to the *other* person.
  On the registration path it could enrol a passkey against the wrong member. Every use of the
  connection now holds the lock. Writes always did, so an invite could never be double-consumed.
- **A crash mid-registration could lock out the person you invited.** Spending the invite and
  writing their passkey were two separate commits; a restart in between burned the invite with no
  credential stored. They are now one transaction, so a failure leaves the invite redeemable.

### Added

- **A Herbs & Spices aisle**, between Cupboard and Frozen. Oregano, thyme, cumin, sage, dill,
  chives and tarragon all used to land in "Other" — the catch-all telling you the map had no
  home for them. Works with no LLM key, from the built-in map alone. A one-off migration re-files
  what it can now place, but only rows sitting in "Other" with no icon: anything filed elsewhere
  was put there by a person and is not ours to move.
- **A density you can choose**, in Settings beside Theme and Accent. Dense fits about fifteen
  rows a screen where comfortable fits eleven — one glance instead of two, halfway down an aisle.
  It's per device, so your phone and someone else's can differ.
- **Counts on each shelf label**, following the items still to get, so they tick down as you shop.
- **The basket is a drawer**, closed by default. By the end of a shop the done pile is most of the
  list, and a section that starts empty and swells to four-fifths of the screen pushes the handful
  of things you still need off the bottom — which is the one thing this list must never do.

### Changed

- **The list runs edge to edge**, with sticky shelf labels carrying the grouping the card edges
  used to. Labels now sit on the same left edge as the item chips.
- **The "frequently added" tray is text pills** instead of fixed-width tiles, which ellipsised to
  "Smoked S…" — the one thing a shortcut must never do. Capped at two measured rows, with the rest
  behind a counter.
- **Motion is quieter and closer to your thumb.** The check-off used to fly the row across the
  whole viewport into the basket: 2373px in 300ms, which is about seven times faster than the eye
  can track, so it read as a smear that left the screen 50ms in and passed behind every shelf
  label on the way. It's gone. The strike-through now draws across the name in 150ms, the row
  collapses where it stands, and nothing in the app runs longer than 400ms — spent once a shop, on
  clearing the basket. Reduced motion now substitutes rather than strips: travel goes, colour and
  opacity still move.
- Checking an item off used to destroy the row on the same tick, so its own confirmation never
  played. The row now stays put for the length of the strike before it leaves.

### Fixed

- **A hung request could wedge the app offline forever.** No request had a timeout, and the queue
  reuses one in-flight promise — so a request that hangs rather than fails (a restarted server
  behind a proxy, a captive portal, a backgrounded PWA) parked the queue permanently. The app kept
  accepting check-offs, the counter climbed, nothing was ever sent, and only a reload recovered it.
  Requests now time out, and the sync stream reconnecting drains the queue instead of just clearing
  the banner.
- **Edits could vanish if storage failed.** Every change was applied to the screen and then written
  to the offline queue with nobody checking the write. If IndexedDB faulted — quota, private
  browsing, an evicted database — the item sat on your shelf, was never queued, and disappeared at
  the next refresh. Add five things in the car park, arrive with an empty list. Failures now roll
  back and say so.
- **An offline check-off could be silently dropped** when the server recognised the item as one you
  already had: queued changes named a row id the server had replaced, 404'd, and were discarded.
- **A drag could leave the list un-draggable.** If the pointer stream ended without a release —
  switching apps mid-drag, a system dialog — the controller stayed armed and refused every later
  drag until a reload.
- **"Lemons" then "Lemon" made two rows** (the reverse order worked). The singular/plural fold is
  now symmetric, and an exact match always wins — which also stops a stray "asparagu" mangling
  "asparagus".
- Several items were filed by the wrong word: "vanilla yogurt" and "herbal tea" as spices,
  "spiced rum" away from Drinks, "cinnamon swirl" away from Bakery.
- Two rapid check-offs could swap the rows under your thumb.
- An item with a note is no longer taller than one without, so the list keeps an even rhythm.

## [0.2.2] - 2026-08-10

### Added

- `trug doctor [args…]` — runs `trug-doctor` inside the container from wherever you are. The
  documented recovery commands were `docker compose exec trug trug-doctor recover …`, which only
  works from the directory holding the compose file. After a one-line install that directory is
  `~/.trug` and nobody is standing in it, so the lockout escape hatch answered
  "no configuration file provided: not found" — at precisely the moment you can't get in.

### Fixed

- Re-adding something you'd already checked off made the whole list jitter. The optimistic row was
  fabricated in `Other` under a new id while the struck-through row was still in the basket — so
  the name was on screen twice — and the server's reply (the original row, reactivated, in its real
  aisle) then destroyed that row and inserted another elsewhere. Three overlapping height changes
  for one logical move. A name already on the list now moves the row that's there.
- The basket card eases shut instead of vanishing in a frame, and the scroll container no longer
  anchors mid-animation — the browser was adjusting scroll position to hold one node still, which
  moved everything else.
- The installer's closing message now explains that `localhost` is the address that prompts for a
  passkey and the only one where an account can be created, while the network link skips passkeys
  and shares a single identity across everyone who opens it.

## [0.2.1] - 2026-08-10

### Fixed

- Settings → About showed `0.1.5` on the 0.2.0 release. The in-app version comes from
  `web/package.json` (injected as `__APP_VERSION__`), nothing bumps it automatically, and the
  release runbook never mentioned it — so tagging shipped an image reporting the previous
  release to everyone. The release workflow now refuses to publish when the tag,
  `web/package.json` and `server/pyproject.toml` disagree.
- `server/pyproject.toml` had been `0.1.0` since the first release. All three now move together.

## [0.2.0] - 2026-08-10

### Added

- A one-line installer (`install.sh`): `curl -fsSL .../install.sh | sh`. It writes `~/.trug`,
  generates all four tokens up front, pulls the image, starts it, waits for a real answer on
  `/healthz`, installs the `trug` command, and prints the bootstrap token. It never installs
  Docker — it names your options and stops — and re-running it is the repair path: no live token
  is regenerated and `data/` is left alone. `TRUG_HOME`, `TRUG_PORT`, `TRUG_REF`,
  `TRUG_HEALTH_TIMEOUT`, `TRUG_IMAGE` and `NO_COLOR` override the defaults. A 32-bit Raspberry Pi
  OS is caught before anything is pulled.
- A `trug` command with six verbs: `up`, `down`, `status`, `logs`, `share`, `set-origin`.
  `trug share` prints a LAN link with the MCP token in it plus a terminal QR; `trug set-origin`
  writes `TRUG_ORIGIN`, derives `TRUG_RP_ID`, restarts and checks the result, refusing a bare
  hostname, a plain-`http` origin, a URL with a path, or an IP address. `status` and `set-origin`
  pass `trug-doctor`'s exit code through.
- `trug-doctor` check `origin.host_is_ip`. For loopback-by-IP it hands over the exact fix; for a
  LAN IP it offers no remedy env, because no setting fixes it, and points at `trug share` or a
  real name instead.
- `docs/remote-access.md`, covering how other people actually reach your instance: the
  zero-setup LAN share link, Tailscale Serve, your own domain with a private-address DNS record,
  and Cloudflare Tunnel.
- Raspberry Pi install notes: the 64-bit OS requirement, the `docker` group logout, and keeping
  the database off the SD card.

### Fixed

- The installer's closing message could hand out a VPN address as "on the wifi". Its address
  lookup had drifted from `trug share`'s and was missing the interface scan, so on Linux it fell
  straight through to the routing table — which returns the VPN's source whenever a VPN carries
  the default route. Both now prefer a private address on a real interface.
- A duplicated key in `.env` is resolved the way Compose resolves it — last wins. Both readers
  took the first, so appending a line to override an earlier one (which genuinely works, because
  `env_file` takes the last) made `trug share` hand out a token the running server rejects.
- 32-bit Raspberry Pi OS is caught on a Pi 4/400/CM4, where `arm_64bit=1` is the default and
  `uname -m` reports `armv8l` rather than `armv7l`. 32-bit x86 too.
- `trug set-origin` no longer leaves a world-readable temp file holding all four secrets if it is
  interrupted.
- `trug share` warns, when `ufw` is running on Linux, that ufw is *not* protecting the published
  port. Docker forwards published ports past ufw's rules entirely, so a default-deny policy gives
  a false sense of what is reachable. The `DOCKER-USER` chain is where a real restriction goes.
- `trug share` picks a real private-range address on a real interface before falling back to the
  routing table. The default route's source is the VPN's address whenever a VPN carries it — a
  Tailscale exit node, or a full-tunnel WireGuard — which answers from the box and nowhere else.

- A failed passkey ceremony is no longer reported as the bare string "Registration failed". The
  server returns a distinguishable `Passkey verification failed`, and the gate turns that into the
  likely cause — the address you're on and the origin Trug is configured for disagree — plus the
  command that confirms it. This is the most common first-deploy failure, and it used to arrive
  with no cause and no next step.
- A confirmed server rejection is never relabelled "you're offline". `navigator.onLine` gets stuck
  false on captive portals, and an answer from the server is proof it isn't the network.
- `origin.observed_host_mismatch` no longer warns about being reached on a LAN IP, which is the
  share link working as designed — it used to make `trug status` exit 1 on every healthy install
  and suggest an IP as the RP ID.
- The doctor treats all of `127.0.0.0/8` as loopback, not just the three usual spellings. Debian
  and Raspberry Pi OS map the machine's own hostname to `127.0.1.1`, which was being diagnosed as
  a remote address needing HTTPS.
- The release workflow refuses to publish under an image name that doesn't match the one
  `docker-compose.yml` and `install.sh` send users to, so a release from a fork can't go green
  while changing nothing for anyone.
- The image carries its own OCI labels, including the commit it was built from. They previously
  came from the base image and named `astral-sh/uv`, so there was no way to tell what a running
  container actually contained.

### Changed

- The sign-in gate now says *why* a passkey can't be created instead of blaming the browser: an
  insecure address points you at `trug share` and notes the "Not Secure" chip is expected; an IP
  address tells you to open `localhost`. On a blocked address it no longer starts a claim ceremony
  it can't finish, and the address explanation outranks the "this trug hasn't been claimed yet"
  copy.
- `rp_id.matches_origin` no longer recommends an IP address as the RP ID, which was advice the
  next check failed you for taking.
- Docs lead with the one-liner: `docs/install.md` gains a **One command** section ahead of Railway
  and Compose, remote access rung one is now `trug share`, and the Pi's SSH tunnel is demoted to a
  footnote — the trial path needs no passkey, so it's no longer a load-bearing step.
- Restructured the README around the ways in — the one-liner, Railway, Docker, and getting the
  rest of the household on it — and moved the reference material into `docs/`: install (now
  including a Raspberry Pi section), remote access, configuration, operations, integrations, and
  troubleshooting. `docs/restore.md` is folded into `docs/operations.md`.

## [0.1.0] - 2026-08-08

### Added

- Offline-first, installable PWA shopping list — one screen grouped into
  store-order aisles, with optimistic updates that keep working with no signal
  and sync when you're back.
- Passkey (WebAuthn) sign-in with an invite-based household roster: no
  passwords, no fixed user list. A one-time bootstrap token claims the first
  account; everyone else joins through a single-use invite link and creates a
  passkey.
- A built-in OAuth 2.1 authorization server plus an MCP server at `/mcp`, so
  assistants like Claude can read and edit the list — link a connector with just
  the URL, no client id or secret to paste.
- Voice-ring capture webhook at `/api/capture` (multipart or JSON) for the
  Pebble Index 01, parsing spoken items straight onto the list and broadcasting
  them to connected phones over Server-Sent Events.
- Bring-your-own-key LLM enrichment (item icons + aisle grouping), fully
  optional — a first-class no-key mode uses a built-in icon map, and the ring
  falls back to a heuristic splitter, so nothing core depends on an LLM.
- Catppuccin theming across all four flavours, with Mocha/Latte following the
  system light/dark preference, plus a configurable accent colour.
- An in-process rate limiter on the sensitive auth endpoints (bootstrap claim,
  passkey login/register, invite redemption, OAuth token), with an explicit
  trusted-proxy-hops boundary.
- The `trug-doctor` diagnostic and recovery CLI — checks config, prints tokens,
  and mints invites or re-opens the first-user claim for lockout recovery
  (`--json`, `--redact` supported).
- Single-container deployment via Docker, Docker Compose, and Railway, with a
  multi-arch (`amd64` + `arm64`) image published to GHCR on tagged releases.

[Unreleased]: https://github.com/maxdraki/trug/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/maxdraki/trug/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/maxdraki/trug/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/maxdraki/trug/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/maxdraki/trug/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/maxdraki/trug/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/maxdraki/trug/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/maxdraki/trug/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/maxdraki/trug/compare/v0.1.5...v0.2.0
[0.1.0]: https://github.com/maxdraki/trug/releases/tag/v0.1.0
