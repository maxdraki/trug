# Restoring from a backup

To restore Trug from a snapshot produced by [`scripts/backup-sample.sh`](../scripts/backup-sample.sh): stop the container (`docker compose down`), copy your chosen backup file over the live database at `data/trug.db` (for example `cp /backups/trug-2026-08-05.db data/trug.db`, and delete any stale `data/trug.db-wal` and `data/trug.db-shm` sidecar files so they don't shadow the restored data), then start it again (`docker compose up -d`). Trug reads the restored file on boot and your list is back exactly as it was in the snapshot.
