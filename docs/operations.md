# Running it

Backups, restores, upgrades, and the doctor.

## Where you run these

Two installs, two shapes of command. Getting this wrong is the most common thing that goes wrong
on this page.

**The installer.** [The one-liner](install.md#one-command) puts the compose file in `~/.trug` and
the `trug` command on your `PATH`. `trug` knows where the install is, so run it from anywhere.
Where there's no verb for what you want, name the directory yourself:

```sh
docker compose --project-directory ~/.trug pull
```

**A clone.** The compose file is the clone, so `cd` into it first and drop the
`--project-directory`.

Everything below is written the installer way, with the clone version alongside where it differs.
Run a bare `docker compose …` anywhere else and Docker looks for a compose file in the directory
you happen to be standing in:

```
no configuration file provided: not found
```

On Railway, substitute `railway run` or an exec into the service.

## The doctor

Trug ships a diagnostic that knows what a healthy instance looks like:

```sh
trug doctor                             # or: trug status, which adds the container state
```

From a clone, in the clone:

```sh
docker compose exec trug trug-doctor
```

Two names in one line there, and both are load-bearing: `trug` is the compose service, and
`trug-doctor` is the command inside it. `trug doctor` hands everything after it straight to
`trug-doctor`, so every flag below works either way.

It prints the resolved configuration and the current tokens, and checks the things that fail
quietly — chiefly a `TRUG_RP_ID` or `TRUG_ORIGIN` that doesn't match the URL people actually load,
which breaks every passkey ceremony without an error in the logs, the UI, or the healthcheck.

Exit codes are a contract you can script against:

| Code | Meaning |
| --- | --- |
| `0` | Healthy. |
| `1` | Warnings — works, but fragile. An unpinned token, a single enrolled member. |
| `2` | Errors. Something is broken and passkeys probably don't work. |

Every non-OK line says exactly what to change. Two flags worth knowing: `--redact` hides token
values before you paste output into an issue, and `--json` produces a machine-readable report
where each check has a stable `id` and, where the fix is mechanical, a `remedy.env` map you can
apply directly.

### Recovery

You never edit the SQLite file by hand. Anyone with host access runs the recovery CLI — host
access *is* the recovery credential, so there's no extra secret to keep:

```sh
# Primary escape hatch: mint an invite link with no session. Preserves everything.
trug doctor recover --invite NAME

# Total lockout, every device gone: re-open the one-time first-user claim.
trug doctor recover --reset-bootstrap

# Stolen device: kill every session.
trug doctor recover --revoke-sessions
```

From a clone, put `docker compose exec trug trug-doctor` where `trug doctor` is, and run it in the
clone.

`--invite` needs no session and works when nobody can sign in, so prefer it whenever any member is
still reachable. `--reset-bootstrap` is for the case where every device is lost: it re-opens the
first-user claim without deleting anything, and refuses while an enrolled credential still exists
unless you pass `--force`. Bare `recover` lists the options and changes nothing.

If you re-open the claim and then don't use it, close the window — until you do, anyone holding
the bootstrap token can claim an account:

```sh
trug doctor recover --cancel-reset
```

## Backup

Everything is in one SQLite file (WAL mode) in the data directory beside the compose file —
`~/.trug/data` from the installer, `./data` in a clone — so this is easy. But do use
`.backup` rather than `cp`. A plain copy taken while the container is running can miss the
write-ahead log and give you a stale or broken database.

```sh
sqlite3 ~/.trug/data/trug.db ".backup $HOME/.trug/backups/trug-$(date +%F).db"
```

The installer makes `~/.trug/backups` for you. From a clone the two paths are `./data/trug.db` and
wherever you want the snapshot — and `~` won't expand inside sqlite3's quoted argument, which is
why `$HOME` is spelled out there.

That's an online snapshot and it's safe to run against a live container. Push it somewhere else
afterwards if you like:

```sh
rclone copy ~/.trug/backups/trug-$(date +%F).db gdrive:trug-backups
```

[`scripts/backup-sample.sh`](../scripts/backup-sample.sh) wraps this up for a nightly cron job. It
defaults to a clone's layout, so on an installer box give it both paths:

```sh
TRUG_DB_PATH=~/.trug/data/trug.db TRUG_BACKUP_DIR=~/.trug/backups /path/to/backup-sample.sh
```

If Trug is on a Raspberry Pi with the database on the SD card, treat this as required rather than
prudent. SD cards wear out.

## Restore

Restoring means stopping the container. A live server holding the write-ahead log while the file
changes underneath it is how you get a half-restored database.

```sh
trug down
cp ~/.trug/backups/trug-2026-08-05.db ~/.trug/data/trug.db
rm -f ~/.trug/data/trug.db-wal ~/.trug/data/trug.db-shm
trug up
```

From a clone: `docker compose down` / `docker compose up -d`, with `./data/trug.db` in the middle.

The third line is the one people miss. Those sidecar files belong to the database you just
replaced, and if you leave them there they'll shadow the restored data.

Trug reads the restored file on boot and the list is back exactly as it was in the snapshot.

## Upgrading

Your list, passkeys, and settings live in the data directory, not the image, so they survive an
upgrade, and Trug runs any database migrations automatically on boot. Take a [backup](#backup)
first anyway — migrations are not reversible by going back to the old image.

**Using the published image**, which is what you want. There's no `trug` verb for the pull, so
this one names the directory:

```sh
docker compose --project-directory ~/.trug pull   # fetch the new image from GHCR
trug up                                           # recreate the container on it
```

From a clone that's `docker compose pull && docker compose up -d`, in the clone.

**Building from source**, if you run the `build: .` path from a clone:

```sh
git pull
docker compose up -d --build
```

Either way, confirm it came back up and see the current version:

```sh
trug status
```

For reproducible upgrades, pin a version instead of `:latest`. From a clone, set
`image: ghcr.io/maxdraki/trug:v0.1.1` in `docker-compose.yml` and bump it deliberately. Don't do
that by hand in `~/.trug/docker-compose.yml` — the installer rewrites that file every run and your
pin goes with it. Re-run the installer with the version instead, which is safe on a live instance:

```sh
curl -fsSL https://raw.githubusercontent.com/maxdraki/trug/main/install.sh \
  | TRUG_IMAGE=ghcr.io/maxdraki/trug:v0.1.1 sh
```

The [releases](https://github.com/maxdraki/trug/releases) and [CHANGELOG](../CHANGELOG.md) say what
each version changes.

On Railway, a connected repo redeploys automatically on push; otherwise hit **Deploy** on the
service.

## Health

There's a healthcheck endpoint if you want to point something at it:

```sh
curl -fsS http://localhost:8000/healthz     # -> {"status":"ok"}
```

The container has its own `HEALTHCHECK` on the same endpoint, so `trug status` — or
`docker compose ps` in a clone — will tell you whether Trug thinks it's well. Note that a healthy
container can still have broken passkeys — the healthcheck doesn't know what your origin is.
That's the doctor's job.
