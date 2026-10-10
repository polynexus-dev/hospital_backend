#!/bin/sh
# Nightly backup of the database and uploaded files, run by the `backup`
# service in docker-compose.yml. Writes to the backups folder (BACKUP_DIR in
# .env — point it at a NAS or external drive so backups survive a disk failure).
#
#   docker compose exec backup sh /backup.sh now      back up right away
#
# Each run: hms-db-<time>.dump (PostgreSQL, checked readable before it's kept),
# hms-files-<time>.tar.gz (uploaded documents), license-<time>.lic, and
# LAST-BACKUP.txt with the result. Files older than BACKUP_KEEP_DAYS are removed.
set -u
DEST=/backups
KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
AT="${BACKUP_TIME:-02:00}"
export PGPASSWORD="$POSTGRES_PASSWORD"

run_backup() {
  stamp=$(date +%Y%m%d-%H%M)
  partial="$DEST/.hms-db-$stamp.dump.partial"
  if pg_dump -h postgres -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f "$partial" \
     && pg_restore --list "$partial" >/dev/null \
     && mv "$partial" "$DEST/hms-db-$stamp.dump" \
     && tar -czf "$DEST/hms-files-$stamp.tar.gz" -C /hms-media .; then
    [ -f /config/license.lic ] && cp /config/license.lic "$DEST/license-$stamp.lic"
    find "$DEST" -maxdepth 1 -type f \( -name 'hms-db-*.dump' -o -name 'hms-files-*.tar.gz' -o -name 'license-*.lic' \) \
      -mtime +"$KEEP_DAYS" -delete
    echo "$(date '+%Y-%m-%d %H:%M') OK hms-db-$stamp.dump" > "$DEST/LAST-BACKUP.txt"
    echo "Backup OK: hms-db-$stamp.dump ($(du -h "$DEST/hms-db-$stamp.dump" | cut -f1))"
    return 0
  fi
  rm -f "$partial"
  echo "$(date '+%Y-%m-%d %H:%M') FAILED - see: docker compose logs backup" > "$DEST/LAST-BACKUP.txt"
  echo "Backup FAILED" >&2
  return 1
}

if [ "${1:-}" = "now" ]; then
  run_backup
  exit $?
fi

trap 'exit 0' TERM INT
echo "Nightly backups at $AT (server time) into the backups folder; keeping $KEEP_DAYS days."
done_today=""
while :; do
  today=$(date +%Y-%m-%d)
  if [ "$(date +%H:%M)" = "$AT" ] && [ "$done_today" != "$today" ]; then
    run_backup
    done_today=$today
  fi
  sleep 30 & wait $!
done
