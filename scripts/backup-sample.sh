#!/usr/bin/env bash
#
# Sample nightly backup for Trug's SQLite database.
#
# Run this on the HOST (needs the sqlite3 CLI); with docker compose the host-side
# database path is ./data/trug.db.
#
# Trug keeps everything in a single SQLite file (WAL mode). A consistent
# snapshot is a one-liner with sqlite3's online `.backup`, which is safe to run
# while the container is live. Copy this script, adjust the paths, and wire it
# into cron (or a systemd timer):
#
#   # crontab -e  ->  nightly at 03:15
#   15 3 * * *  /path/to/trug/scripts/backup-sample.sh
#
set -euo pipefail

# Resolve paths relative to the repo root, not the caller's CWD. cron runs with
# CWD=$HOME, so without this the relative ./data/trug.db default below would
# resolve to ~/data/trug.db — and sqlite3's `.backup` would cheerfully CREATE an
# empty database there and back up nothing. Anchoring to the script's own
# location keeps the default correct no matter where cron (or you) invokes it.
cd "$(dirname "$0")/.."

# Path to the live database. This runs on the HOST, where the ./data volume
# from docker-compose.yml surfaces the DB at ./data/trug.db (that is the default
# below). Override with TRUG_DB_PATH if your host path differs.
DB_PATH="${TRUG_DB_PATH:-./data/trug.db}"

# Where snapshots land locally before being synced offsite. The default
# /backups must already exist and be writable by the user running this (root, or
# a dir you created and chowned) — set TRUG_BACKUP_DIR to somewhere writable
# otherwise.
BACKUP_DIR="${TRUG_BACKUP_DIR:-/backups}"

# Refuse loudly if the database isn't where we think it is. Without this,
# sqlite3 `.backup` would create an empty file at DB_PATH and exit 0 — a backup
# that silently succeeds with no data is worse than one that fails.
if [ ! -f "$DB_PATH" ]; then
  echo "error: no database at $DB_PATH (CWD=$(pwd))." >&2
  echo "       Set TRUG_DB_PATH to the live trug.db, or run from a Trug checkout" >&2
  echo "       whose ./data/trug.db exists. Refusing to back up an empty database." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
DEST="$BACKUP_DIR/trug-$(date +%F).db"

# Online, consistent snapshot — safe to run against a live database.
sqlite3 "$DB_PATH" ".backup '$DEST'"

# Optional: push the snapshot offsite with rclone (e.g. to Google Drive).
# Configure a remote once with `rclone config`, then uncomment:
# rclone copy "$DEST" gdrive:trug-backups

echo "Backed up $DB_PATH -> $DEST"
