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

# Same reasoning as SECRET_KEY above: base.py's defaults for these are
# fixed, checked-into-this-repo placeholders so local dev works with no
# .env at all. Reaching production with either unchanged would mean every
# deployment that forgot to set them encrypts patient data (and hashes
# phone numbers for lookup) under a key anyone can read in this repo's
# history.
_DEV_FERNET_KEY = "t2NvOpAA9rQ6Ud5hsyk6sSLsAILgnltwzOoMfsExWKs="
_DEV_GCM_KEY = "UKErull4TB4qeyWpzXSwrna10cg0exEhKiCdBAa6zAw="

# Resolved the same way apps.core.encryption resolves them: the rotation
# list wins when set, otherwise the single key. Checking only the singular
# would leave FIELD_ENCRYPTION_KEYS_V2="<placeholder>" as a way to boot
# production encrypting under a key that is public in this repo — i.e. the
# rotation settings would be a hole in the check that exists to stop
# exactly that.
#
# The placeholder is rejected anywhere in the list, not just as the
# primary. Keeping it for decryption would mean production is still
# serving data encrypted under a public key; that situation calls for
# re-encrypting before deploying, not for carrying the key forward.
_fernet_keys = FIELD_ENCRYPTION_KEYS or [FIELD_ENCRYPTION_KEY]  # noqa: F405
_gcm_keys = FIELD_ENCRYPTION_KEYS_V2 or [FIELD_ENCRYPTION_KEY_V2]  # noqa: F405

if not any(_fernet_keys) or _DEV_FERNET_KEY in _fernet_keys:
    raise RuntimeError("FIELD_ENCRYPTION_KEY(S) is unset or still the dev placeholder — set a real key in the production environment before starting.")
if not any(_gcm_keys) or _DEV_GCM_KEY in _gcm_keys:
    raise RuntimeError("FIELD_ENCRYPTION_KEY_V2(S) is unset or still the dev placeholder — set a real key in the production environment before starting.")
if not FIELD_HASH_KEY or "change-me" in FIELD_HASH_KEY or "insecure" in FIELD_HASH_KEY:  # noqa: F405
    raise RuntimeError("FIELD_HASH_KEY is still a placeholder — set a real key in the production environment before starting.")

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
CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=[
        "https://*.hms.polynexus.in",
        "https://hms.polynexus.in",
        "http://*.hms.polynexus.in",
        "http://hms.polynexus.in",
    ] + CORS_ALLOWED_ORIGINS,  # noqa: F405
)

# Django -> PostgreSQL over TLS (WASA / ISO 27001 A.8.20). DB_SSLMODE (or
# ?sslmode= on DATABASE_URL) sets it. A database on this host or on the
# compose network (localhost, a unix socket, a one-word service name such as
# "postgres" or "db") never leaves the machine, so it may stay plain; any
# other host carries credentials and patient data over a real network, and
# production refuses to start until the connection is encrypted.
_db = DATABASES["default"]  # noqa: F405
if "postgresql" in _db.get("ENGINE", ""):
    _db_options = _db.setdefault("OPTIONS", {})
    _sslmode = env.str("DB_SSLMODE", default=_db_options.get("sslmode", ""))  # noqa: F405
    if _sslmode:
        _db_options["sslmode"] = _sslmode
    _db_host = str(_db.get("HOST") or "")
    _db_is_local = _db_host in ("", "localhost", "127.0.0.1", "::1") or _db_host.startswith("/") or "." not in _db_host and ":" not in _db_host
    if not _db_is_local and _sslmode not in ("require", "verify-ca", "verify-full"):
        raise RuntimeError(
            f"The database at {_db_host} is reached over the network without TLS — set DB_SSLMODE=require "
            "(or verify-full with the server's CA) before starting."
        )

# The OpenAPI schema and Swagger UI map every endpoint: staff only in
# production (drf-spectacular's default is AllowAny). CI generates the
# schema it scans with `manage.py spectacular` instead (.github/workflows/zap-scan.yml).
SPECTACULAR_SETTINGS = {**SPECTACULAR_SETTINGS, "SERVE_PERMISSIONS": ["rest_framework.permissions.IsAdminUser"]}  # noqa: F405

# Layer 2 (apps.core.middleware.PayloadEncryptionMiddleware) is not optional in
# production: without it, the only protection between the browser and Django
# is TLS, which ends at the proxy.
PAYLOAD_ENCRYPTION_ENABLED = env.bool("PAYLOAD_ENCRYPTION_ENABLED", default=True)  # noqa: F405
if not PAYLOAD_ENCRYPTION_ENABLED:
    raise RuntimeError("PAYLOAD_ENCRYPTION_ENABLED must be True in production.")
