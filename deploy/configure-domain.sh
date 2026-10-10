#!/usr/bin/env bash
# Polynexus HMS — put the installation on a domain (e.g. hms.hospital.com),
# with HTTPS. Run from the install folder on a Linux server:
#
#   sudo ./configure-domain.sh
#
# Before running: the domain's DNS record must point at this server
# (internal DNS for in-hospital use, public DNS for internet access).
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE="docker compose -f docker-compose.yml"
say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31mError: %s\033[0m\n' "$*" >&2; exit 1; }
set_env() {  # set_env NAME VALUE — replace the line, or add it
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else printf '%s=%s\n' "$1" "$2" >> .env; fi
}

[ -f .env ] || die "Run this from the install folder (no .env here). Install first with install.sh."
set -a; . ./.env; set +a

read -rp "Domain staff will use (e.g. hms.hospital.com): " DOMAIN
[[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || die "That doesn't look like a domain name."
getent hosts "$DOMAIN" >/dev/null || echo "Warning: $DOMAIN doesn't resolve from this server yet. Add the DNS record first if it's missing."

echo
echo "How should HTTPS be set up?"
echo "  1) The hospital's own certificate (files you already have)"
echo "  2) Let's Encrypt — free, renews automatically (server must be reachable from the internet on port 80)"
echo "  3) No certificate for now — plain HTTP (internal network only)"
read -rp "Choose 1, 2 or 3: " MODE

say "Pointing the installation at $DOMAIN"
set_env ALLOWED_HOSTS "$DOMAIN,localhost,web"
set_env CORS_ALLOWED_ORIGINS "https://$DOMAIN,http://$DOMAIN"
set_env CSRF_TRUSTED_ORIGINS "https://$DOMAIN,http://$DOMAIN"
mkdir -p certs

case "$MODE" in
  1)
    read -rp "Path to the certificate file (PEM, including the intermediate chain): " CRT
    read -rp "Path to the private key file (PEM): " KEY
    [ -f "$CRT" ] && [ -f "$KEY" ] || die "Certificate or key file not found."
    cp "$CRT" certs/tls.crt.new && cp "$KEY" certs/tls.key.new
    say "Checking the certificate with nginx"
    # nginx already sees certs/ (read-only); test the HTTPS config against the .new files.
    if ! $COMPOSE run --rm --no-deps nginx sh -c \
         "sed -e 's#certs/tls.crt#certs/tls.crt.new#' -e 's#certs/tls.key#certs/tls.key.new#' /etc/nginx/hms/https.conf > /etc/nginx/conf.d/hms.conf && nginx -t" ; then
      rm -f certs/tls.crt.new certs/tls.key.new
      die "nginx rejected the certificate/key (wrong pair, or not PEM). Nothing was changed."
    fi
    mv certs/tls.crt.new certs/tls.crt && mv certs/tls.key.new certs/tls.key
    chmod 600 certs/tls.key
    set_env SECURE_SSL_REDIRECT True
    ;;
  2)
    [ "${HTTP_PORT:-80}" = "80" ] || die "Let's Encrypt needs HTTP on port 80, but HTTP_PORT=$HTTP_PORT. Free port 80, set HTTP_PORT=80 in .env, and run this again."
    read -rp "Email for Let's Encrypt expiry notices: " EMAIL
    [ -n "$EMAIL" ] || die "An email is required."
    $COMPOSE up -d nginx
    say "Requesting a certificate for $DOMAIN (needs internet)"
    $COMPOSE --profile letsencrypt run --rm --entrypoint certbot certbot certonly --webroot -w /var/www/acme \
      -d "$DOMAIN" --email "$EMAIL" --agree-tos --no-eff-email -n \
      || die "Let's Encrypt couldn't verify $DOMAIN. Check that its public DNS points at this server and port 80 is open to the internet."
    $COMPOSE --profile letsencrypt run --rm --entrypoint sh certbot -c \
      "cp /etc/letsencrypt/live/$DOMAIN/fullchain.pem /certs/tls.crt && cp /etc/letsencrypt/live/$DOMAIN/privkey.pem /certs/tls.key"
    set_env COMPOSE_PROFILES letsencrypt   # keeps the renewal service running from now on
    set_env SECURE_SSL_REDIRECT True
    ;;
  3)
    set_env SECURE_SSL_REDIRECT False
    ;;
  *) die "Choose 1, 2 or 3." ;;
esac

say "Restarting"
set -a; . ./.env; set +a
$COMPOSE up -d
$COMPOSE up -d --force-recreate web worker beat nginx

if [ "$MODE" = 3 ]; then URL="http://$DOMAIN"; else URL="https://$DOMAIN"; fi
[ "$MODE" = 3 ] && [ "${HTTP_PORT:-80}" != "80" ] && URL="$URL:$HTTP_PORT"
[ "$MODE" != 3 ] && [ "${HTTPS_PORT:-443}" != "443" ] && URL="$URL:$HTTPS_PORT"
echo
echo "Done. Staff can now open $URL/"
[ "$MODE" = 2 ] && echo "The certificate renews automatically (checked twice a day)."
[ "$MODE" = 1 ] && echo "When the certificate is replaced, run this again (or overwrite certs/tls.crt and tls.key: picked up within an hour)."
