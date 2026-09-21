"""
Base settings shared by every environment. Environment-specific overrides
live in dev.py / prod.py — never put secrets or environment-specific
values here, read them from the environment instead.
"""
import os
from datetime import timedelta
from pathlib import Path

import environ
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="django-insecure-change-me-in-env")

# Field-level encryption (Part A #2) — see apps.core.encryption /
# apps.core.fields. FIELD_ENCRYPTION_KEY must be a urlsafe-base64 32-byte
# Fernet key (`Fernet.generate_key()`); FIELD_HASH_KEY is an independent
# secret used only for one-way blind-index hashes (exact-match lookups on
# encrypted columns, e.g. phone number matching) and is never used to
# recover data, so it can be any sufficiently random string. The dev
# defaults below are fixed (not randomly generated per-run) so local data
# stays decryptable across restarts, and are rejected outright in prod.py
# if left unchanged.
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", default="t2NvOpAA9rQ6Ud5hsyk6sSLsAILgnltwzOoMfsExWKs=")
FIELD_HASH_KEY = env("FIELD_HASH_KEY", default="dev-only-insecure-blind-index-key-change-me")

# Rotation list, newest key first — the primary encrypts, every key is
# tried on decrypt, so old ciphertext stays readable until each row is
# re-saved under the new key. apps.core.encryption has always looked for
# these (see _build_fernet / _build_gcm_keys) but nothing here defined
# them, so the getattr always returned None and rotation was impossible
# to configure: putting FIELD_ENCRYPTION_KEYS in a .env populated
# os.environ without ever becoming a Django setting. Empty by default,
# which falls back to the single-key settings above.
FIELD_ENCRYPTION_KEYS = env.list("FIELD_ENCRYPTION_KEYS", default=[])

# AES-256-GCM key for new field encryption (apps.core.encryption._gcm_*) —
# FIELD_ENCRYPTION_KEY/Fernet above is only still consulted to decrypt
# values written before this key existed. Must be a urlsafe-base64
# 32-byte key: `base64.urlsafe_b64encode(os.urandom(32))`. Same
# fixed-dev-default / rejected-in-prod treatment as FIELD_ENCRYPTION_KEY.
FIELD_ENCRYPTION_KEY_V2 = env("FIELD_ENCRYPTION_KEY_V2", default="UKErull4TB4qeyWpzXSwrna10cg0exEhKiCdBAa6zAw=")
# Rotation list for the AES-256-GCM key — same newest-first semantics as
# FIELD_ENCRYPTION_KEYS above (apps.core.encryption._gcm_encrypt always
# encrypts under keys[0]).
FIELD_ENCRYPTION_KEYS_V2 = env.list("FIELD_ENCRYPTION_KEYS_V2", default=[])

DEBUG = env.bool("DEBUG", default=False)

_allowed_hosts = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
for h in [".hms.polynexus.in", "app.hms.polynexus.in", "localhost", "127.0.0.1"]:
    if h not in _allowed_hosts:
        _allowed_hosts.append(h)
ALLOWED_HOSTS = _allowed_hosts


# Application definition

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
    "django_celery_beat",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.patients",
    "apps.telephony",
    "apps.enquiries",
    "apps.appointments",
    "apps.communications",
    "apps.automation",
    "apps.feedback",
    "apps.analytics",
    "apps.integrations",
    "apps.referrals",
    "apps.packages",
    "apps.tpa",
    "apps.facilities",
    "apps.opd",
    "apps.ipd",
    "apps.nursing",
    "apps.laboratory",
    "apps.radiology",
    "apps.pharmacy",
    "apps.emergency",
    "apps.ot",
    "apps.icu",
    "apps.bloodbank",
    "apps.finance",
    "apps.hr",
    "apps.billing",
    "apps.inventory",
    "apps.saas_admin",
    "apps.privacy",
    "apps.abdm",
]



INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    # Layer-2 payload encryption -- must be FIRST so it can rewrite
    # request.body before SecurityMiddleware or any other middleware reads it.
    # Transparent no-op when PAYLOAD_ENCRYPTION_ENABLED=False.
    "apps.core.middleware.PayloadEncryptionMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.TenantMiddleware",
    "apps.core.middleware.AuditMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


# Database
# Payload encryption toggle (Layer 2 -- application-layer encrypt/decrypt).
# Set True in production to hide all API payloads from browser DevTools.
# Set False (default) in dev / Postman -- middleware becomes a no-op.
PAYLOAD_ENCRYPTION_ENABLED = env.bool("PAYLOAD_ENCRYPTION_ENABLED", default=False)

if env.bool("USE_SQLITE", default=False):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": os.environ.get("DB_ENGINE", "django.db.backends.postgresql"),
            "NAME": os.environ.get("POSTGRES_DB", "hospital_crm"),
            "HOST": os.environ.get("DB_HOST", "localhost"),
            "PORT": os.environ.get("DB_PORT", "5432"),
            "USER": os.environ.get("POSTGRES_USER", "postgres"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "admin"),
            # Reuse connections across requests instead of opening a fresh TCP+auth
            # handshake every time (default is 0 = no reuse). django-tenants'
            # set_tenant() just issues `SET search_path` on the existing
            # connection when switching schemas, so this is safe to combine with
            # multi-tenancy — the schema switch itself stays cheap.
            # DB_HOST/DB_PORT point at PgBouncer (SESSION pool mode) in
            # docker-compose, not straight at Postgres — see the pgbouncer
            # service comment in docker-compose.yml before changing pool mode.
            # Note: Must be 0 when using PgBouncer to prevent session pool exhaustion.
            "CONN_MAX_AGE": int(os.environ.get("DB_CONN_MAX_AGE", 0)),
        }
    }



AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization — Marathi / Hindi / English per the feature catalogue
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

LANGUAGES = [
    ("en", "English"),
    ("hi", "Hindi"),
    ("mr", "Marathi"),
]


# Static / media files. Swapping MEDIA to S3 for production (document
# vault, call recordings) means changing only the "default" backend here —
# add django-storages and point it at S3, no application code changes.
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Django REST Framework
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
        "apps.core.permissions.RoleBasedModelPermissions",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardResultsPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        # No-op for any view that doesn't set `throttle_scope` (e.g.
        # HospitalTokenObtainPairView's "login" — see that view's
        # docstring for why it's declared there and not as a per-view
        # throttle_classes override).
        "rest_framework.throttling.ScopedRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        # General baseline for the whole API — generous enough that normal
        # front-desk/telephony usage never notices it.
        "anon": "60/minute",
        "user": "300/minute",
        # HospitalTokenObtainPairView (login) — has no auth to gate it,
        # which makes it the default brute-force-guessing target; a tight
        # per-IP ceiling matters far more here than on any authenticated
        # endpoint. 5/minute stops rapid password guessing without
        # meaningfully affecting a real user who mistypes their password
        # once or twice.
        "login": "5/minute",
        # Per-tenant resource isolation for expensive, synchronous
        # operations that scale with a hospital's own data volume rather
        # than with request count — bulk CSV/FHIR/MIS export and CSV
        # enquiry import (see apps.enquiries.views.EnquiryViewSet.
        # bulk_import, apps.integrations.views.DataExportView/
        # FHIRExportView, apps.analytics.views.MISExportView). All
        # hospitals share one deployment with no per-tenant compute
        # quota, so nothing otherwise stops one hospital's staff running
        # a large export/import repeatedly from tying up app-server
        # workers and DB connections that every other hospital's ordinary
        # requests also depend on. 20/hour is well above any legitimate
        # one-off use (running a report a few times while checking output)
        # but bounds the worst case.
        "heavy_ops": "20/hour",
    },
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
    ),
}

SIMPLE_JWT = {
    # Part A #7 — "session timeout on clinical workstations". Was 8h/7d: a
    # front-desk/nurse-station terminal left logged in stayed a valid
    # session for a week via silent refresh, well past any single shift.
    # 1h/12h means an abandoned browser session dies within the same
    # working day even if nobody touches it, while ROTATE_REFRESH_TOKENS
    # below keeps re-auth invisible to a genuinely active user. This is a
    # partial mitigation, not the full control: a stolen *access* token is
    # only ever good for up to an hour, but nothing here notices an idle
    # tab and locks it mid-shift — that needs a frontend inactivity timer,
    # which is a separate (frontend) change from this one.
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=env.int("ACCESS_TOKEN_LIFETIME_HOURS", default=1)),
    "REFRESH_TOKEN_LIFETIME": timedelta(hours=env.int("REFRESH_TOKEN_LIFETIME_HOURS", default=12)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Polynexus Hospital CRM API",
    "DESCRIPTION": "Phase 1 backend — call/enquiry/appointment capture, patient 360, omnichannel messaging, automation, feedback, MIS.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:5173",
    ],
)
CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https?://([a-zA-Z0-9-]+\.)?hms\.polynexus\.in(:[0-9]+)?$",
]


GEMINI_API_KEY = env("GEMINI_API_KEY", default="")

# Self-hosted Ollama server — see apps.communications.llm_router. Used
# ONLY to classify free-text patient messages into a fixed set of known
# intents for the 24x7 assistant (never to compose what a patient reads,
# diagnose, or give medical advice — see llm_router.py's module docstring).
# The default below is Ollama's standard local port; point OLLAMA_BASE_URL
# at your actual VM (e.g. "http://10.x.x.x:11434") via .env in every real
# deployment — classify_free_text_intent() already degrades to "unclear"
# (shows the main menu) if the server is unreachable, so a wrong/missing
# address here fails safe, it just won't route free text yet.
OLLAMA_BASE_URL = env("OLLAMA_BASE_URL", default="http://localhost:11434")
OLLAMA_MODEL = env("OLLAMA_MODEL", default="llama3")
OLLAMA_TIMEOUT_SECONDS = env.int("OLLAMA_TIMEOUT_SECONDS", default=6)


# Cache — backs DRF's request throttling (see REST_FRAMEWORK below). Redis,
# not Django's default LocMemCache: LocMemCache is per-process, so with more
# than one app-server worker (any real production deployment) each worker
# would count requests independently and the effective rate limit becomes
# `configured rate x worker count` — not the hard ceiling it's meant to be.
# Same Redis instance Celery already requires, separate logical DB (1, not
# Celery's 0) so cache keys and broker traffic don't collide.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("CACHE_URL", default="redis://localhost:6379/1"),
        # Forces the older RESP2 wire protocol. redis-py 5+ defaults to
        # attempting a HELLO handshake (RESP3) on connect, which errors
        # with "unknown command 'HELLO'" against any Redis server older
        # than 6.0 (verified against this project's own local dev Redis,
        # 3.0.504) — RESP2 is a strict subset every version understands,
        # including whatever a given hospital's ops team ends up running,
        # so there's no reason to require RESP3 here.
        "OPTIONS": {"protocol": 2},
    }
}

# Celery
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

CELERY_BEAT_SCHEDULE = {
    # §1 — missed-call / RNR chase list escalation.
    "escalate-overdue-callbacks": {
        "task": "apps.telephony.tasks.escalate_overdue_callbacks",
        "schedule": 300.0,  # every 5 minutes
    },
    # §2 — enquiry SLA ageing / auto-escalation.
    "escalate-overdue-enquiries": {
        "task": "apps.enquiries.tasks.escalate_overdue_enquiries",
        "schedule": 900.0,  # every 15 minutes
    },
    # §4 — 24h / 2h appointment reminders.
    "send-appointment-reminders": {
        "task": "apps.appointments.tasks.send_appointment_reminders",
        "schedule": 900.0,
    },
    # §4 — auto no-show detection past the grace period.
    "mark-overdue-appointments-as-no-show": {
        "task": "apps.appointments.tasks.mark_overdue_appointments_as_no_show",
        "schedule": 1800.0,  # every 30 minutes
    },
    # §6 — generic automation task escalation.
    "escalate-overdue-tasks": {
        "task": "apps.automation.tasks.escalate_overdue_tasks",
        "schedule": 900.0,
    },
    # Retention — preventive-care / follow-up recall sweep, once daily.
    "sweep-patient-recalls": {
        "task": "apps.automation.tasks.sweep_patient_recalls",
        "schedule": crontab(hour=8, minute=0),
    },
    # §1/§12 — owner's daily WhatsApp MIS, evening dispatch.
    "send-daily-mis-to-owners": {
        "task": "apps.analytics.tasks.send_daily_mis_to_owners",
        "schedule": crontab(hour=20, minute=0),
    },
    # §15 — bi-directional HIS sync.
    "sync-his-data": {
        "task": "apps.integrations.tasks.sync_all_hospitals_his_data",
        "schedule": 3600.0,  # hourly
    },
    # DPDP Act 2023 (Part A #12) — completes right-to-erasure requests by
    # hard-deleting records past their soft-delete grace period.
    "purge-expired-soft-deleted-records": {
        "task": "apps.automation.tasks.purge_expired_soft_deleted_records",
        "schedule": crontab(hour=3, minute=0),
    },
    # DPDP Act 2023 (Part A #12) — stale, never-converted leads.
    "purge-stale-unconverted-enquiries": {
        "task": "apps.automation.tasks.purge_stale_unconverted_enquiries",
        "schedule": crontab(hour=3, minute=15),
    },
    # Lead-quality scoring (apps.enquiries.scoring) — retrains each
    # hospital's model fresh from its own closed enquiries and rescoes
    # every open one. Runs before the 3am purge jobs above so a lead
    # doesn't get purged on a score that's a day stale.
    "recompute-enquiry-scores": {
        "task": "apps.enquiries.tasks.recompute_enquiry_scores",
        "schedule": crontab(hour=2, minute=30),
    },
}


# --- Hospital CRM domain settings -----------------------------------------

# DPDP Act 2023 default retention window for interaction data (days).
# Overridable per-hospital in future phases; a single default is enough for P1.
DEFAULT_DATA_RETENTION_DAYS = env.int("DEFAULT_DATA_RETENTION_DAYS", default=365 * 3)

# How long a soft-deleted patient-identifying/clinical record (Part A #1)
# stays soft-deleted before apps.automation.tasks.
# purge_expired_soft_deleted_records removes it outright — completing a
# right-to-erasure request (Part A #12) rather than leaving it soft-deleted
# forever. Deliberately short and separate from DEFAULT_DATA_RETENTION_DAYS
# above: this only ever applies to a record someone already decided should
# go, not to live clinical data subject to medical-record retention law.
SOFT_DELETE_PURGE_GRACE_DAYS = env.int("SOFT_DELETE_PURGE_GRACE_DAYS", default=30)

# DPDP Act 2023 data-principal rights (Part A #11) — see apps.privacy.
# These are operational SLAs this codebase enforces, not a specific legal
# deadline quoted from the DPDP Rules 2025 (that's a compliance/legal
# question outside what a settings default can responsibly assert) —
# 30 days is a conservative, commonly-used baseline; a hospital's legal
# counsel should confirm the number that actually applies to it.
DATA_RIGHTS_REQUEST_SLA_DAYS = env.int("DATA_RIGHTS_REQUEST_SLA_DAYS", default=30)
GRIEVANCE_SLA_DAYS = env.int("GRIEVANCE_SLA_DAYS", default=30)

# Appointment reminder offsets, hours-before-slot.
APPOINTMENT_REMINDER_OFFSETS_HOURS = [24, 2]

# Base URL of the patient-facing React app, used to build WhatsApp/SMS deep
# links (paperless registration, feedback response) — e.g. https://app.polynexus.in
PUBLIC_APP_URL = env("PUBLIC_APP_URL", default="http://localhost:3000")

# Communication provider selection — see apps.communications.adapters.
WHATSAPP_PROVIDER = env("WHATSAPP_PROVIDER", default="stub")
SMS_PROVIDER = env("SMS_PROVIDER", default="stub")
EMAIL_PROVIDER = env("EMAIL_PROVIDER", default="stub")

# Only used when WHATSAPP_PROVIDER=aws (AWSEndUserMessagingWhatsAppProvider).
# AWS credentials themselves come from boto3's standard credential chain
# (env vars / ~/.aws/credentials / IAM role), not from Django settings.
WHATSAPP_AWS_REGION = env("WHATSAPP_AWS_REGION", default="ap-south-1")
WHATSAPP_ORIGINATION_PHONE_NUMBER_ID = env("WHATSAPP_ORIGINATION_PHONE_NUMBER_ID", default="")
WHATSAPP_META_API_VERSION = env("WHATSAPP_META_API_VERSION", default="v19.0")

# Telephony provider selection — see apps.telephony.adapters.
TELEPHONY_PROVIDER = env("TELEPHONY_PROVIDER", default="stub")

# HIS connector selection — see apps.integrations.his.
HIS_CONNECTOR = env("HIS_CONNECTOR", default="stub")

# ABDM (Ayushman Bharat Digital Mission — ABHA linking + HIE-CM consent)
# and NHCX (National Health Claims Exchange) gateway selection — see
# apps.abdm.gateway. "stub" (the only implementation that exists so far)
# raises GatewayNotConfigured on every call rather than fabricating a
# success; connecting either later is a matter of adding a real
# ABDMGateway/NHCXGateway subclass and pointing these at it, plus filling
# in the sandbox/production credentials below once ABDM/NHCX onboarding
# for this hospital's Health Facility Registry entry is complete.
ABDM_GATEWAY = env("ABDM_GATEWAY", default="stub")
ABDM_BASE_URL = env("ABDM_BASE_URL", default="")
ABDM_CLIENT_ID = env("ABDM_CLIENT_ID", default="")
ABDM_CLIENT_SECRET = env("ABDM_CLIENT_SECRET", default="")
# Health Information Provider ID, assigned once this facility is
# registered on ABDM's Health Facility Registry.
ABDM_HIP_ID = env("ABDM_HIP_ID", default="")

NHCX_GATEWAY = env("NHCX_GATEWAY", default="stub")
NHCX_BASE_URL = env("NHCX_BASE_URL", default="")
NHCX_PARTICIPANT_CODE = env("NHCX_PARTICIPANT_CODE", default="")


# --- Logging (Part A #3: no patient data in logs by default) -------------
#
# Deliberately explicit rather than relying on Django's built-in logging
# config. Django's default wires the `django.request` logger to an
# AdminEmailHandler on any 5xx — which, on a Django app whose views mostly
# take and return patient data, means emailing whoever's in ADMINS a page
# containing the failing request's full POST body (mobile numbers,
# national IDs, clinical notes) every time a request errors. ADMINS is not
# set anywhere in this project (grep confirms it), so that handler is
# currently a no-op — but "currently a no-op because nobody's configured
# ADMINS yet" is not a control, it's an accident waiting for someone to set
# ADMINS for on-call paging and get patient data in their inbox as a side
# effect. Routing straight to console/file instead removes that trap.
#
# This does NOT redact patient fields from application log lines — no
# logging filter can know which fields in an arbitrary `logger.info(...)`
# call are patient data. The actual control is upstream of logging: don't
# pass patient-identifying values (mobile, national_id_number, address,
# diagnosis/symptoms/notes, or a whole model instance/serializer.data) into
# a log call. If a log line needs to reference a record, log its id, not
# its content.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        # Overrides Django's built-in AdminEmailHandler wiring for this
        # logger specifically — see the module comment above.
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
