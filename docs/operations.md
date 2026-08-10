# Running it

Backups, restores, upgrades, and the doctor. Everything here assumes Docker Compose from a
clone — on Railway, substitute `railway run` or an exec into the service.

## The doctor

Trug ships a diagnostic that knows what a healthy instance looks like:

```sh
docker compose exec trug trug-doctor
```

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
docker compose exec trug trug-doctor recover --invite NAME

# Total lockout, every device gone: re-open the one-time first-user claim.
docker compose exec trug trug-doctor recover --reset-bootstrap

# Stolen device: kill every session.
docker compose exec trug trug-doctor recover --revoke-sessions
```

`--invite` needs no session and works when nobody can sign in, so prefer it whenever any member is
still reachable. `--reset-bootstrap` is for the case where every device is lost: it re-opens the
first-user claim without deleting anything, and refuses while an enrolled credential still exists
unless you pass `--force`. Bare `recover` lists the options and changes nothing.

## Backup

Everything is in one SQLite file (WAL mode) in the `./data` volume, so this is easy — but do use
`.backup` rather than `cp`. A plain copy taken while the container is running can miss the
write-ahead log and give you a stale or broken database.

```sh
sqlite3 ./data/trug.db ".backup /backups/trug-$(date +%F).db"
```

That's an online snapshot and it's safe to run against a live container. Push it somewhere else
afterwards if you like:

```sh
rclone copy /backups/trug-$(date +%F).db gdrive:trug-backups
```

[`scripts/backup-sample.sh`](../scripts/backup-sample.sh) wraps this up for a nightly cron job.

If Trug is on a Raspberry Pi with the database on the SD card, treat this as required rather than
prudent. SD cards wear out.

## Restore

Restoring means stopping the container. A live server holding the write-ahead log while the file
changes underneath it is how you get a half-restored database.

```sh
docker compose down
cp /backups/trug-2026-08-05.db data/trug.db
rm -f data/trug.db-wal data/trug.db-shm
docker compose up -d
```

The third line is the one people miss. Those sidecar files belong to the database you just
replaced, and if you leave them there they'll shadow the restored data.

Trug reads the restored file on boot and the list is back exactly as it was in the snapshot.

## Upgrading

Your list, passkeys, and settings live in the `./data` volume, so they survive an upgrade, and
Trug runs any database migrations automatically on boot. Take a [backup](#backup) first anyway —
migrations are not reversible by going back to the old image.

**Using the published image**, which is what you want:

```sh
docker compose pull      # fetch the new image from GHCR
docker compose up -d     # recreate the container on it
```

**Building from source**, if you run the `build: .` path from a clone:

```sh
git pull
docker compose up -d --build
```

Either way, confirm it came back up and see the current version:

```sh
docker compose exec trug trug-doctor
```

For reproducible upgrades, pin a version instead of `:latest` — set
`image: ghcr.io/maxdraki/trug:v0.1.1` in `docker-compose.yml` and bump it deliberately. The
[releases](https://github.com/maxdraki/trug/releases) and [CHANGELOG](../CHANGELOG.md) say what
each version changes.

On Railway, a connected repo redeploys automatically on push; otherwise hit **Deploy** on the
service.

## Health

There's a healthcheck endpoint if you want to point something at it:

```sh
curl -fsS http://localhost:8000/healthz     # -> {"status":"ok"}
```

The container has its own `HEALTHCHECK` on the same endpoint, so `docker compose ps` will tell you
whether Trug thinks it's well. Note that a healthy container can still have broken passkeys — the
healthcheck doesn't know what your origin is. That's the doctor's job.
