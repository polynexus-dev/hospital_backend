from .base import *  # noqa: F401,F403

DEBUG = True

CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True

REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["login"] = "100/minute"

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
