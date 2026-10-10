#!/usr/bin/env bash
# Polynexus HMS — upgrade an existing Linux installation. Works offline.
#
#   sudo ./upgrade.sh [install-dir]     (run from the NEW unpacked bundle; default install-dir /opt/hms)
#
# Order: database backup, load new images, update deploy files (your .env,
# licence and data are kept), migrate, restart.
set -euo pipefail
BUNDLE="$(cd "$(dirname "$0")" && pwd)"
TARGET="$(cd "${1:-/opt/hms}" && pwd)"
say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31mError: %s\033[0m\n' "$*" >&2; exit 1; }

[ -f "$TARGET/.env" ] || die "$TARGET has no .env: is that where HMS is installed? Pass the folder: ./upgrade.sh /path/to/hms"
NEW_VERSION=$(cat "$BUNDLE/VERSION")
cd "$TARGET"
COMPOSE="docker compose -f docker-compose.yml"
set -a; . ./.env; set +a
OLD_VERSION="$HMS_VERSION"

# --- 1. Backup first ------------------------------------------------------------
STAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p backups
say "Backing up the database (version $OLD_VERSION)"
$COMPOSE up -d postgres
until $COMPOSE exec -T postgres pg_isready -U "$POSTGRES_USER" >/dev/null 2>&1; do sleep 2; done
$COMPOSE exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/hms-backup.dump
$COMPOSE cp postgres:/tmp/hms-backup.dump "backups/hms-$OLD_VERSION-$STAMP.dump"
cp .env "backups/env-$STAMP"
[ -f config/license.lic ] && cp config/license.lic "backups/license-$STAMP.lic"
echo "Backup: $TARGET/backups/hms-$OLD_VERSION-$STAMP.dump"

# --- 2. New images and deploy files --------------------------------------------
say "Loading images for $NEW_VERSION"
docker load -i "$BUNDLE/images.tar"
for f in docker-compose.yml env.template install.sh install.ps1 upgrade.sh upgrade.ps1 configure-domain.sh configure-domain.ps1 backup.sh RUNBOOK.md README.md VERSION; do
  [ -f "$BUNDLE/$f" ] && [ "$BUNDLE" != "$TARGET" ] && cp "$BUNDLE/$f" "$TARGET/$f"
done
sed -i "s/^HMS_VERSION=.*/HMS_VERSION=$NEW_VERSION/" .env

# --- 3. Migrate and restart ------------------------------------------------------
say "Migrating the database"
$COMPOSE run --rm -e RUN_MIGRATIONS=0 web python manage.py migrate --noinput
say "Restarting services"
$COMPOSE up -d --remove-orphans
echo
echo "Upgraded $OLD_VERSION -> $NEW_VERSION."
echo "To roll back: set HMS_VERSION=$OLD_VERSION in .env, restore backups/hms-$OLD_VERSION-$STAMP.dump (see RUNBOOK.md), then: $COMPOSE up -d"
