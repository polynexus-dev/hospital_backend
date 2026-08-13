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

DEBUG = env.bool("DEBUG", default=False)

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])


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
]


INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
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
    },
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
    ),
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=8),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
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


GEMINI_API_KEY = env("GEMINI_API_KEY", default="")



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
}


# --- Hospital CRM domain settings -----------------------------------------

# DPDP Act 2023 default retention window for interaction data (days).
# Overridable per-hospital in future phases; a single default is enough for P1.
DEFAULT_DATA_RETENTION_DAYS = env.int("DEFAULT_DATA_RETENTION_DAYS", default=365 * 3)

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
