#!/bin/sh
set -e

echo "Applying database migrations..."
python manage.py migrate --fake-initial --noinput

echo "Seeding demo data (idempotent - skips if already seeded)..."
python manage.py seed_demo_data

echo "Starting backend process: $@"
exec "$@"
