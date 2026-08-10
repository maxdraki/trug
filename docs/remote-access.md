# Getting your household on it

Trug on your own machine is a shopping list for one person. This page is how the rest of the
house gets in.

There is one rule behind everything here. **A browser only creates a passkey in a secure
context** — HTTPS, or literally `localhost` — and `TRUG_RP_ID` can't be an IP address. So a phone
at `http://192.168.1.5:8000` will load the list quite happily and then fail to make an account,
usually with no error worth reading. That isn't a Trug limitation and there's no flag to turn it
off.

Two ways round it, and the first one takes no setup at all.

## Rung one: `trug share`

Good enough for a Saturday. No accounts, no installs, works on your wifi in about thirty seconds.

On the machine running Trug:

```sh
trug share
```

It prints your LAN address with the MCP token already in it —
`http://192.168.1.5:8000/?token=…` — plus a QR of the same link, so a phone points its camera at
the terminal and it's in.

Installed by hand? There's no `trug` command, so build the URL yourself: your machine's LAN
address (on a Mac, `ipconfig getifaddr en0`), not `localhost`, and the MCP token from `.env` or
**Settings → Connections**.

The whole list works: adding, checking off, notes, categories, drag reorder, search, and live sync
between phones. Trug strips the token from the URL and keeps it in local storage, so it's a
one-time paste and the address bar doesn't hold a credential afterwards.

What you're giving up, and it's worth being straight about it:

- **It's a shared machine credential, not a personal login.** Everyone using the link is the same
  principal, and items land attributed to `mcp` rather than to a person. Nobody has to look at
  that, mind: `added_by` is stored but rendered nowhere in the app. It's a fact in the database,
  not a mark on the screen.
- **That token also grants MCP write access.** Don't send it to anyone you wouldn't give the
  keys to.
- **"Anyone on this network" means whatever network you're on.** On a box that stays home that's
  the house. On a laptop it's the cafe wifi too, so think before you run this in one.
- **On Linux, `ufw` is not protecting this.** Docker publishes a container port by forwarding it,
  and ufw filters traffic addressed to the host — two paths that never meet. So the link is
  reachable by anything that can route to the machine no matter what your ufw rules say, and
  `ufw allow` does nothing either way. To genuinely restrict it, write the rule in the
  `DOCKER-USER` chain. `trug share` says this when it sees ufw running.
- **No account settings.** Members, Devices, Connections and AI enrichment all need a real
  passkey session and will return 401.
- **No home-screen install, and no service worker.** Service workers need a secure context, so:
  no installable PWA, no push, and the app can't cold-start offline. A tab that's already open is
  fine — tick things off in the shop and they sync when you're back, because the queue lives in
  IndexedDB and waits there until that tab has a connection again.

If that's fine, you're done. If you want real accounts, keep reading.

## Rung two: a real HTTPS name

Give the box a name browsers trust and everything works properly — passkeys, per-person
attribution, invites, home-screen install, the lot.

| Route | Per-device setup | Reachable from | Cost |
| --- | --- | --- | --- |
| [Tailscale Serve](#tailscale-serve) | install the app, sign in | anywhere | free |
| [Your own domain, with Caddy](#your-own-domain-with-caddy) | **nothing** | your wifi only | a domain |
| [Cloudflare Tunnel](#cloudflare-tunnel) | nothing | the public internet | free, needs a CF account |
| [Railway](install.md#one-click-on-railway) | nothing | the public internet | ~$5/month |

Then, whichever you pick, [point Trug at the new name](#tell-trug-its-new-name). That step is not
optional and its failure mode is silent.

### Tailscale Serve

The least work, and it works away from the house as well as on it.

```sh
tailscale serve --bg 8000
```

That's the whole thing. Tailscale gives the machine a name like
`https://yourbox.tail1234.ts.net` with a real certificate, renewed for you, and nothing is
exposed to the internet — traffic goes over your tailnet.

The cost is per device: everyone needs the Tailscale app installed and signed into your tailnet.
For a household that's a one-off, and it buys you a list that works from the shop as well as the
kitchen.

Use `tailscale funnel` instead of `serve` only if you actually want the instance on the public
internet.

### Your own domain, with Caddy

The only option where nobody else installs anything, and the one people don't realise is
available.

Point a public DNS record at your machine's **private** address:

```
trug.yourname.com.  A  192.168.1.5
```

That address is unroutable from the internet, so nobody outside your house can reach it. But
Let's Encrypt will still issue a certificate for the name over a DNS challenge, which needs no
inbound connection at all. The result is a name every browser and phone trusts, resolving only on
your wifi, with nothing to install on anyone's device.

Caddy does the certificate part in a few lines:

```
trug.yourname.com {
	reverse_proxy localhost:8000
	tls {
		dns cloudflare {env.CF_API_TOKEN}
	}
}
```

Two things to know before you commit to this one:

- **The DNS challenge needs an API token** for a provider Caddy can talk to, and a Caddy build
  that includes that provider's plugin — the stock binary doesn't ship them. The `caddy:builder`
  image or `xcaddy build --with github.com/caddy-dns/cloudflare` gets you one.
- **Some routers block this.** "DNS rebind protection" refuses public names that resolve to
  private addresses. It's usually a checkbox you can turn off, occasionally a dead end. Test with
  `dig trug.yourname.com` from inside the house before you build anything on it.

Set `TRUG_TRUSTED_PROXY_HOPS=1` once Trug is behind Caddy, and bind Trug to loopback so nothing
can reach it around the proxy — see [configuration](configuration.md#behind-a-proxy).

### Cloudflare Tunnel

No ports forwarded, no inbound firewall changes, and a public hostname with TLS terminated by
Cloudflare. There's a commented `cloudflared` service in `docker-compose.yml` ready to uncomment:
drop your tunnel token into `.env` as `CLOUDFLARE_TUNNEL_TOKEN` and point the tunnel at
`http://trug:8000` in the Cloudflare Zero Trust dashboard.

This one does put your instance on the public internet, so set `TRUG_TRUSTED_PROXY_HOPS=1` and
read the [rate limiting](configuration.md#rate-limiting) notes.

### Not self-signed certificates

They look like the free option and they aren't. A self-signed certificate means installing a root
certificate on every phone that will ever use Trug, and on iOS that's a configuration profile plus
a trust toggle buried three levels into Settings. You'll spend longer on it than on any route
above, and you'll do it again for every new device.

## Tell Trug its new name

One command, on the machine running Trug:

```sh
trug set-origin https://trug.tail1234.ts.net
```

It writes the origin, derives the bare host as `TRUG_RP_ID`, restarts, and runs the doctor to
check the result — and it refuses a bare hostname, a plain-`http` origin, a URL with a path on the
end, or an IP address, all of which break passkeys silently.

By hand it's two settings, and they have to agree with what the browser shows:

```sh
TRUG_ORIGIN=https://trug.tail1234.ts.net   # the full origin, exactly as loaded
TRUG_RP_ID=trug.tail1234.ts.net            # the bare host — no scheme, no port
```

Then restart (`docker compose up -d`) and check it took:

```sh
docker compose exec trug trug-doctor
```

The doctor exists mostly for this. A mismatch here breaks every passkey ceremony with no error in
the logs, the UI, or the healthcheck, so it's worth the ten seconds.

Three traps:

- **`TRUG_RP_ID` is the bare host.** No `https://`, no port. The doctor's `rp_id.bare_host` check
  will fail you if you put either in, which is a kindness.
- **Changing `TRUG_RP_ID` invalidates existing passkeys.** Credentials are bound to the hostname
  they were created under, so anyone enrolled on `localhost` re-enrols once against the new name.
  Do this before you invite the household, not after.
- **Everyone has to be re-pointed at the new name, once.** The old LAN link and the new HTTPS name
  are separate browser origins with separate local storage, so a phone still sitting on
  `http://192.168.1.5:8000` keeps its token, stays a machine principal, and carries on exactly as
  before. From that phone it looks like `set-origin` did nothing at all. Send everyone the new URL
  and have them open it there.

## About the token fallback

The gate's "use an access token" path signs you in as a *machine principal*, not a human owner. It
does not create an account and the session is heavily gated: Members (the only way to onboard
people), Connections, Devices and AI enrichment all require a passkey session and return 401 on a
bearer, and there's no sign-out.

The list, capture, and MCP do work on a bearer, which is what makes rung one useful. But the first
real account still has to be claimed over HTTPS or literal `localhost` — the token path is machine
and emergency access, not a way around the rule.
