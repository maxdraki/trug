#!/bin/sh
# Fix ownership of the data mount, then drop to the unprivileged user.
# Platform volumes (Railway, plain docker named volumes, Pi bind mounts)
# arrive root-owned; the app runs as uid 999 (trug).
set -e

DATA_DIR="$(dirname "${TRUG_DB_PATH:-/data/trug.db}")"

if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA_DIR"
  chown -R trug:trug "$DATA_DIR"
  exec gosu trug "$@"
fi

exec "$@"
