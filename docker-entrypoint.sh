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

echo "Ensuring SaaS-company roles and accounts exist (idempotent)..."
python manage.py seed_saas_company --password "${SAAS_SEED_PASSWORD:-changeme123}" || true

echo "Ensuring required portal login accounts exist..."
python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')
django.setup()
from apps.accounts.models import User
from apps.core.models import Hospital

users = [
    ('admin@hms.polynexus.in', 'System Admin', None, True, True),
    ('owner@demo-hospital.example', 'Hospital Owner / Admin', 'demo-hospital', True, False),
    ('frontdesk@demo-hospital.example', 'Front Desk / Reception', 'demo-hospital', False, False),
    ('doctor@demo-hospital.example', 'OPD Doctor', 'demo-hospital', False, False),
    ('operator@demo-hospital.example', 'Telephony Operator', 'demo-hospital', False, False),
]

h = Hospital.objects.filter(slug='demo-hospital').first()

for email, role_desc, h_slug, is_staff, is_super in users:
    u = User.objects.filter(email=email).first()
    if not u:
        u = User.objects.create_user(
            email=email,
            first_name=role_desc.split()[0],
            last_name='User',
            is_staff=is_staff,
            is_superuser=is_super,
            is_saas_admin=(email == 'admin@hms.polynexus.in'),
            hospital=h if h_slug else None,
        )
        u.set_password('changeme123')
        u.save()
        print(f'   [+] Created login account: {email} ({role_desc})')
    else:
        u.is_staff = is_staff
        u.is_superuser = is_super
        u.is_saas_admin = (email == 'admin@hms.polynexus.in')
        u.hospital = h if h_slug else None
        if not u.check_password('changeme123'):
            u.set_password('changeme123')
        u.save()
        print(f'   [OK] Verified login account: {email} ({role_desc})')
" || true

echo "Starting backend process: $@"
exec "$@"
