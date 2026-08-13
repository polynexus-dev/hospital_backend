# Polynexus Hospital CRM — Backend

Django + Django REST Framework backend for the Phase 1 ("Nothing is lost", weeks 1–6) scope of the
Patient Engagement Platform: call capture, enquiry pipeline, patient 360, appointment scheduling,
omnichannel messaging, automation/escalation, feedback/NPS, and the owner's daily MIS.

The React frontend lives separately in `Hospital/Frontend`.

## Stack

- Django 5 + Django REST Framework, PostgreSQL, Celery + Redis, JWT auth (`djangorestframework-simplejwt`)
- Row-level multi-tenancy — one `core.Hospital` row per hospital, every domain model scoped to it
- `drf-spectacular` for a live OpenAPI contract at `/api/schema/swagger-ui/`

## Project layout

```
config/                  Django project: settings/{base,dev,prod}.py, urls.py, celery.py
apps/
  core/                  Hospital (tenant), Department, TenantScopedModel, AuditLog, tenancy middleware
  accounts/              Custom User, Role (backed by Django Group), JWT auth
  patients/              Patient 360, Document vault, TimelineEvent
  telephony/             Call capture, callback/RNR queue, IVR routing, screen-pop, click-to-call
  enquiries/              Enquiry pipeline, duplicate detection, assignment, SLA/escalation, CSV import
  appointments/          Doctor/Slot/Appointment, clash-free booking, reminders, no-show, paperless registration
  communications/        Unified inbox, templates, consent/opt-out, WhatsApp/SMS/Email adapters
  automation/            Generic Task + EscalationRule, signal-based hooks (e.g. no-show -> recall task)
  feedback/               NPS, Google review routing, complaints, service recovery
  analytics/             Call/enquiry/appointment reports, daily WhatsApp MIS
  integrations/          HIS connector interface (visits/billing sync), self-service CSV export
```

Every external integration (telephony vendor, WhatsApp/SMS/email provider, HIS) is built behind a
small adapter interface with a `stub` implementation that logs instead of calling out. Swapping in
a real vendor is a matter of adding one adapter class and pointing the relevant
`settings.*_PROVIDER` / `settings.HIS_CONNECTOR` value at it — no other code changes.

## Local setup

### Option A — Docker (recommended, matches the on-prem deployment story)

```bash
cp .env.example .env
docker compose up --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py seed_demo_data
docker compose exec web python manage.py createsuperuser   # optional, seed_demo_data already makes one
```

App: http://localhost:8000/admin/ · API docs: http://localhost:8000/api/schema/swagger-ui/

### Option B — local Python + local PostgreSQL/Redis

```bash
python -m venv venv
./venv/Scripts/pip install -r requirements-dev.txt   # includes pytest/factory_boy on top of requirements.txt
cp .env.example .env   # edit DATABASE_URL / CELERY_BROKER_URL to match your local Postgres/Redis
./venv/Scripts/python manage.py migrate
./venv/Scripts/python manage.py seed_demo_data
./venv/Scripts/python manage.py runserver
```

In separate terminals:
```bash
./venv/Scripts/celery -A config worker --loglevel=info
./venv/Scripts/celery -A config beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

`seed_demo_data` creates a demo hospital, an OPD department, a doctor with two weeks of generated
slots, two patients, reminder/feedback templates, and an admin login
(`admin@demo-hospital.example`, password via `--admin-password`, default `changeme123`).

## Tests

```bash
./venv/Scripts/python -m pytest
```

Covers tenant scoping (a hospital can't see another hospital's data), enquiry duplicate detection
and assignment, slot clash prevention, and the reminder scheduling task. Needs a working Postgres
connection (`DATABASE_URL` in `.env`) — pytest-django creates and tears down a throwaway test
database against it.

## Known gaps / next steps

- **RBAC**: `accounts.Role` is backed by a Django `Group`; `assign_role()` keeps group membership in
  sync. `DjangoModelPermissions` is *not* wired globally yet — turning it on requires roles to have
  real permissions assigned first (via Django admin), otherwise every non-superuser is locked out of
  writes. Enable per-viewset or globally once a hospital's roles are populated.
- **Field-level / record-visibility rules** (§13) beyond hospital-level tenant scoping are not built —
  the Role model is the hook point for that when it's prioritized.
- **Storage**: document vault / call recordings use local disk (`STORAGES["default"]`) in dev. Swap
  to S3 for production by adding `django-storages` and pointing that setting at it.
- **Providers**: telephony, SMS (DLT), email (SES/Brevo), and the HIS connector all still default to
  `stub` (logs/returns nothing instead of sending/fetching) until a vendor is chosen and credentials
  are in place. **WhatsApp now has a real adapter** (`AWSEndUserMessagingWhatsAppProvider`, set
  `WHATSAPP_PROVIDER=aws` plus the three `WHATSAPP_*` settings once an AWS End User Messaging Social
  account and WABA phone number exist) — its request/response shape was verified against the boto3
  SDK's service model, but it has not yet been exercised against a live account. See
  `apps/*/adapters.py` and `apps/integrations/connectors.py`.
- **24x7 AI Assistant** (`apps/communications/ai_chatbot.py`): this is a scripted button-flow, not an
  LLM (`GEMINI_API_KEY` exists in settings but nothing calls it yet). As of this pass it reads real
  tenant data — actual `Doctor`/`Slot` rows instead of a hardcoded demo list — and a completed booking
  creates a real `Appointment` via `apps.appointments.services.book_appointment`, the same path the
  front-desk UI uses. It does not understand free-text queries, only the button options it returns.
- **Export**: `apps.integrations` ships open CSV export (§13); FHIR R4 resource export is P2/P4 scope.
