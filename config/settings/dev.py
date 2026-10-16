from .base import *  # noqa: F401,F403

DEBUG = True

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# runserver handles every request on a new thread, and a persistent connection
# (base.py: CONN_MAX_AGE=60) stays open per thread: with the app's polling that
# used up Postgres's 100 connections ("too many clients"). Close per request here;
# production (gunicorn) keeps reusing connections.
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=0)  # noqa: F405
