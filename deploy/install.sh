#!/usr/bin/env bash
# Polynexus HMS — first-time install on a Linux server. Works with no
# internet access: images come from images.tar in this bundle.
#
#   sudo ./install.sh            (from the unpacked bundle folder)
#
# Safe to re-run: existing .env, data and licence are kept. Run it twice on
# a new server: the first run prints the deployment ID and fingerprint to
# send to Polynexus; the second run, with the licence file, finishes.
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE="docker compose -f docker-compose.yml"
say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[31mError: %s\033[0m\n' "$*" >&2; exit 1; }

# --- 1. Prerequisites ---------------------------------------------------------
command -v docker >/dev/null || die "Docker is not installed (https://docs.docker.com/engine/install/)."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required (the 'docker compose' plugin)."
docker info >/dev/null 2>&1 || die "Docker is installed but not running, or this user can't use it (try sudo)."
command -v openssl >/dev/null || die "openssl is required to generate secrets."
[ -s /etc/machine-id ] || die "/etc/machine-id is missing; it identifies this server for licensing."
VERSION=$(cat VERSION 2>/dev/null || echo latest)

# --- 2. Images (offline) ------------------------------------------------------
if [ -f images.tar ]; then
  say "Loading images (version $VERSION)"
  docker load -i images.tar
fi

# --- 3. Configuration and secrets ---------------------------------------------
if [ ! -f .env ]; then
  say "Creating .env with fresh secrets"
  read -rp "Hostname or IP staff will use to reach this server (e.g. hms.hospital.local): " HOST
  [ -n "$HOST" ] || die "A hostname or IP is required."
  sed -e "s|__VERSION__|${VERSION}|" \
      -e "s|__DEPLOYMENT_ID__|$(cat /proc/sys/kernel/random/uuid)|" \
      -e "s|__HOSTS__|${HOST},localhost,web|" \
      -e "s|__ORIGINS__|http://${HOST},https://${HOST}|" \
      -e "s|__POSTGRES_PASSWORD__|$(openssl rand -hex 24)|" \
      -e "s|__SECRET_KEY__|$(openssl rand -hex 32)|" \
      -e "s|__FERNET_KEY__|$(openssl rand -base64 32 | tr '+/' '-_')|" \
      -e "s|__GCM_KEY__|$(openssl rand -base64 32)|" \
      -e "s|__HASH_KEY__|$(openssl rand -hex 32)|" \
      -e "s|__HOST_MACHINE_ID__|/etc/machine-id|" \
      -e "s|__HOST_PRODUCT_UUID__|/sys/class/dmi/id/product_uuid|" \
      env.template > .env
  chmod 600 .env
  echo "Saved .env. Back it up somewhere safe: its keys decrypt patient data."
fi
set -a; . ./.env; set +a

mkdir -p config certs backups
chown 10001:10001 config 2>/dev/null || true   # the app runs as uid 10001 and writes uploaded licences here

say "Starting the database"
$COMPOSE up -d postgres redis

# --- 4. Licence ---------------------------------------------------------------
if [ ! -s config/license.lic ]; then
  FP=$($COMPOSE run --rm --no-deps -e RUN_MIGRATIONS=0 web python manage.py get_machine_fingerprint | tail -1)
  printf 'Deployment ID: %s\nMachine fingerprint: %s\n' "$DEPLOYMENT_ID" "$FP" | tee config/REQUEST-LICENCE.txt
  say "Send the two lines above (saved in config/REQUEST-LICENCE.txt) to Polynexus to get your licence."
  read -rp "Path to your licence file (leave empty to stop here and re-run later): " LIC
  [ -n "$LIC" ] || { echo "Re-run ./install.sh when you have the licence."; exit 0; }
  [ -f "$LIC" ] || die "No file at $LIC"
  cp "$LIC" config/license.lic
  chown 10001:10001 config/license.lic 2>/dev/null || true
fi

# --- 5. Database, hospital and admin account ---------------------------------
say "Preparing the database"
$COMPOSE run --rm -e RUN_MIGRATIONS=0 web python manage.py migrate --noinput

read -rp "Administrator email: " ADMIN_EMAIL
[ -n "$ADMIN_EMAIL" ] || die "An administrator email is required."
say "Activating the licence (you'll be asked for the administrator password)"
$COMPOSE run --rm -e RUN_MIGRATIONS=0 web python manage.py setup_onprem --license /app/config/license.lic --admin-email "$ADMIN_EMAIL"

# --- 6. Start -----------------------------------------------------------------
say "Starting all services"
$COMPOSE up -d
HOST=$(echo "$ALLOWED_HOSTS" | cut -d, -f1)
echo
echo "Done. Open http://${HOST}/ and sign in as ${ADMIN_EMAIL}."
echo "Status: $COMPOSE ps    Logs: $COMPOSE logs -f web"
