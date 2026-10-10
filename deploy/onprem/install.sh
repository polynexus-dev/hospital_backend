#!/usr/bin/env bash
# First-time setup of an on-premise installation. Run from this directory on
# the hospital's server (Linux, Docker Engine + Compose v2):
#
#   ./install.sh
#
# Safe to re-run: existing secrets, data and license are kept.
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE="docker compose -f docker-compose.onprem.yml"
say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31mError: %s\033[0m\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "Docker is not installed."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required."
command -v openssl >/dev/null || die "openssl is required to generate secrets."
[ -f /etc/machine-id ] || die "/etc/machine-id is missing; it identifies this server for licensing."

# --- 1. Secrets ---------------------------------------------------------------
if [ ! -f .env ]; then
  say "Creating .env with fresh secrets"
  read -rp "Hostname or IP staff will use to reach this server (e.g. hms.hospital.local): " HOST
  [ -n "$HOST" ] || die "A hostname or IP is required."
  fernet=$(openssl rand -base64 32 | tr '+/' '-_')
  sed -e "s|__HOSTS__|${HOST},localhost,backend|" \
      -e "s|__ORIGINS__|http://${HOST},https://${HOST}|" \
      -e "s|__POSTGRES_PASSWORD__|$(openssl rand -hex 24)|" \
      -e "s|__SECRET_KEY__|$(openssl rand -hex 32)|" \
      -e "s|__FERNET_KEY__|${fernet}|" \
      -e "s|__GCM_KEY__|$(openssl rand -base64 32)|" \
      -e "s|__HASH_KEY__|$(openssl rand -hex 32)|" \
      env.template > .env
  chmod 600 .env
  echo "Saved .env. Back it up somewhere safe: without its keys, encrypted data can't be read."
else
  echo ".env already exists; keeping it."
fi

# --- 2. Folders (the backend runs as uid 10001) -------------------------------
mkdir -p license media pgdata
chown 10001:10001 license media 2>/dev/null || sudo chown 10001:10001 license media

# --- 3. Images ----------------------------------------------------------------
say "Getting images"
# Offline servers load images from a tar file instead (docker load -i hms-images.tar).
$COMPOSE pull --ignore-pull-failures || true
for img in $($COMPOSE config --images | sort -u); do
  docker image inspect "$img" >/dev/null 2>&1 || die "Image $img is missing: pull it or run: docker load -i hms-images.tar"
done
$COMPOSE up -d postgres redis

# --- 4. License -----------------------------------------------------------------
if [ ! -s license/hospital.lic ]; then
  say "This server's machine fingerprint"
  FP=$($COMPOSE run --rm --no-deps -e RUN_MIGRATIONS=0 backend python manage.py get_machine_fingerprint | tail -1)
  echo "  $FP"
  echo "Send this fingerprint to support to receive your license file."
  read -rp "Path to your license file (leave empty to stop here and re-run later): " LIC
  [ -n "$LIC" ] || { echo "Re-run ./install.sh once you have the license."; exit 0; }
  [ -f "$LIC" ] || die "No file at $LIC"
  cp "$LIC" license/hospital.lic
  chown 10001:10001 license/hospital.lic 2>/dev/null || sudo chown 10001:10001 license/hospital.lic
fi

# --- 5. Database + hospital + owner account -----------------------------------
say "Applying database migrations"
$COMPOSE run --rm -e RUN_MIGRATIONS=0 backend python manage.py migrate --noinput

say "Activating the license and creating the hospital owner account"
$COMPOSE run --rm -e RUN_MIGRATIONS=0 backend python manage.py setup_onprem --license /app/license/hospital.lic

# --- 6. Start -----------------------------------------------------------------
say "Starting the system"
$COMPOSE up -d
echo
echo "Done. Open http://$(grep '^ALLOWED_HOSTS=' .env | cut -d= -f2 | cut -d, -f1)/ and sign in with the owner account."
echo "Renew the license later from Settings → License, or replace license/hospital.lic and run: $COMPOSE restart backend worker"
