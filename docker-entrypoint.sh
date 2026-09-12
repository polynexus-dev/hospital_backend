#!/bin/sh
set -e

echo "Waiting for PostgreSQL database to be ready..."
until python -c "import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev'); django.setup(); from django.db import connection; connection.cursor()" 2>/dev/null; do
  echo "PostgreSQL is unavailable - sleeping 1s"
  sleep 1
done

echo "Applying database migrations..."
python manage.py migrate --noinput

echo "Seeding demo data & accounts (idempotent)..."
python manage.py seed_demo_data --admin-password changeme123 || true

echo "Starting backend process: $@"
exec "$@"
