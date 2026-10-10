#!/bin/sh
# On-premise entrypoint: wait for PostgreSQL, apply migrations, start.
# (Unlike docker-entrypoint.sh, never seeds demo data or demo accounts.)
set -e

until python -c "import django; django.setup(); from django.db import connection; connection.cursor()" 2>/dev/null; do
  echo "Waiting for PostgreSQL..."
  sleep 2
done

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  python manage.py migrate --noinput
fi

exec "$@"
