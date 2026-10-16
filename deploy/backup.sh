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

# With BACKUP_PASSPHRASE set (.env), the dump and the files archive are
# encrypted (AES-256, key from the passphrase via PBKDF2) and only the .enc
# files are kept. Keep the passphrase somewhere other than the backup drive:
# without it the backups can't be restored. Restore:
#   openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:BACKUP_PASSPHRASE -in X.enc -out X
encrypt_in_place() {
  openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:BACKUP_PASSPHRASE -in "$1" -out "$1.enc.partial"     && mv "$1.enc.partial" "$1.enc" && rm -f "$1"
}

run_backup() {
  stamp=$(date +%Y%m%d-%H%M)
  partial="$DEST/.hms-db-$stamp.dump.partial"
  db="$DEST/hms-db-$stamp.dump"
  files="$DEST/hms-files-$stamp.tar.gz"
  if [ -n "${BACKUP_PASSPHRASE:-}" ] && ! command -v openssl >/dev/null 2>&1; then
    echo "$(date '+%Y-%m-%d %H:%M') FAILED - BACKUP_PASSPHRASE is set but openssl is missing in the backup container" > "$DEST/LAST-BACKUP.txt"
    echo "Backup FAILED: openssl not available to encrypt (refusing to write unencrypted backups)" >&2
    return 1
  fi
  if pg_dump -h postgres -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f "$partial"      && pg_restore --list "$partial" >/dev/null      && mv "$partial" "$db"      && tar -czf "$files" -C /hms-media .      && { [ -z "${BACKUP_PASSPHRASE:-}" ] || { encrypt_in_place "$db" && encrypt_in_place "$files"; }; }; then
    [ -f /config/license.lic ] && cp /config/license.lic "$DEST/license-$stamp.lic"
    find "$DEST" -maxdepth 1 -type f \( -name 'hms-db-*.dump*' -o -name 'hms-files-*.tar.gz*' -o -name 'license-*.lic' \)       -mtime +"$KEEP_DAYS" -delete
    name=$(basename "$(ls "$db"* | head -1)")
    echo "$(date '+%Y-%m-%d %H:%M') OK $name" > "$DEST/LAST-BACKUP.txt"
    echo "Backup OK: $name ($(du -h "$DEST/$name" | cut -f1))"
    return 0
  fi
  rm -f "$partial" "$db" "$files" "$db.enc.partial" "$files.enc.partial"
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
