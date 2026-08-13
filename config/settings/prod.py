from .base import *  # noqa: F401,F403

DEBUG = False

# base.py's SECRET_KEY falls back to a fixed, publicly-visible-in-this-repo
# default so `manage.py` works out of the box in local dev with no .env at
# all, and .env.example ships its own equally-placeholder value
# ("change-me-to-a-random-secret-in-every-environment") for the same
# reason. Either reaching production would mean every deployment that
# forgot to actually change it shares the same known signing key for
# sessions and password-reset tokens. Checked as a substring match, not an
# exact-string one — this dev environment's own .env already proves the
# two placeholders differ from each other, and there's no reason a third
# placeholder wouldn't show up somewhere down the line; "does this look
# like nobody set a real value" is the actual property worth checking.
if not SECRET_KEY or "change-me" in SECRET_KEY or "insecure" in SECRET_KEY:  # noqa: F405
    raise RuntimeError("SECRET_KEY is still a placeholder — set a real SECRET_KEY in the production environment before starting.")

SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)  # noqa: F405
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True

# The frontend is a separately-hosted SPA (not served by this Django app),
# so it talks to the API cross-origin — CORS_ALLOWED_ORIGINS already
# covers actual API calls, but Django's own CSRF check (still relevant for
# SessionAuthentication, which DEFAULT_AUTHENTICATION_CLASSES keeps enabled
# alongside JWT) needs the same origins trusted explicitly, or any
# session-authenticated POST/PUT/PATCH/DELETE from the real frontend
# origin gets rejected in production even though CORS allowed the request
# through.
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=CORS_ALLOWED_ORIGINS)  # noqa: F405
