#!/bin/sh
# Fix ownership of the data mount, then drop to the unprivileged user.
# Platform volumes (Railway, plain docker named volumes, Pi bind mounts)
# arrive root-owned; the app runs as uid 999 (trug).
set -e

# Bind the port the platform picked, when there is one — Railway, Render, Fly
# and Heroku all inject PORT and route their healthcheck at it. Normalised here,
# in a real script, rather than interpolated into the CMD line: an unset value
# and an EMPTY one must both fall back to 8000 (Railway can inject empty), and
# the value must be validated before it reaches uvicorn's argv, where a value
# like "8000 --log-level critical" would silently silence every log line.
PORT="${PORT:-8000}"
case "$PORT" in
  ''|*[!0-9]*)
    echo "trug: PORT must be a whole number, got '$PORT'" >&2
    exit 1
    ;;
esac
export PORT

DATA_DIR="$(dirname "${TRUG_DB_PATH:-/data/trug.db}")"

if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA_DIR"
  chown -R trug:trug "$DATA_DIR"
  exec gosu trug "$@"
fi

exec "$@"
