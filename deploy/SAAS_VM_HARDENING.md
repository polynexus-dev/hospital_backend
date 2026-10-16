# Moving the SaaS VM (hms.polynexus.in) to production settings

The VM runs `Backend/docker-compose.yml` with **development** settings. On 16 Oct 2026, `https://hms.polynexus.in/api/v1/<anything>` served Django's debug page. That means:

- error pages show code, settings and local variables (which can include patient data);
- unless `.env` sets its own keys, patient data is encrypted with the **public development keys** in this repo;
- no HTTPS redirect, HSTS or secure cookies, and the Django dev server (`runserver`) serves traffic;
- Postgres (5435) and Redis (6381) were published on every interface (now bound to `127.0.0.1` by default).

Do the steps below **in order**. The key change (step 4) must happen before step 5: production settings refuse to start while the dev keys are configured, even as old keys.

## 1. Back up

```sh
cd <Backend checkout on the VM>
docker compose exec -T db pg_dump -U postgres -d hospital_crm -Fc > ~/hospital_crm-before-prod-$(date +%Y%m%d).dump
```

Copy the dump off the VM.

## 2. Which keys encrypted the data?

```sh
grep -E '^(FIELD_ENCRYPTION_KEY|FIELD_ENCRYPTION_KEY_V2|FIELD_HASH_KEY|SECRET_KEY)=' .env
```

Any line missing, or equal to one of these, means the public dev key is in use:

| Setting | Public dev value |
|---|---|
| `FIELD_ENCRYPTION_KEY` | `t2NvOpAA9rQ6Ud5hsyk6sSLsAILgnltwzOoMfsExWKs=` |
| `FIELD_ENCRYPTION_KEY_V2` | `UKErull4TB4qeyWpzXSwrna10cg0exEhKiCdBAa6zAw=` |
| `FIELD_HASH_KEY` | `dev-only-insecure-blind-index-key-change-me` |

## 3. Generate new keys

```sh
docker compose exec -T web python -c "
import base64, os, secrets
from cryptography.fernet import Fernet
print('FIELD_ENCRYPTION_KEY=' + Fernet.generate_key().decode())
print('FIELD_ENCRYPTION_KEY_V2=' + base64.urlsafe_b64encode(os.urandom(32)).decode())
print('FIELD_HASH_KEY=' + secrets.token_urlsafe(48))
print('SECRET_KEY=' + secrets.token_urlsafe(64))
"
```

Store them in your password manager **before** going on. Losing them makes the encrypted data unrecoverable.

## 4. Re-encrypt the data under the new keys

New key first, current (old) key second. Still development settings at this point:

```sh
docker compose run --rm \
  -e FIELD_ENCRYPTION_KEYS_V2="<new V2>,<old V2>" \
  -e FIELD_ENCRYPTION_KEYS="<new fernet>,<old fernet>" \
  -e FIELD_HASH_KEY="<new hash key>" \
  web python manage.py reencrypt_fields --dry-run     # then again without --dry-run
```

It must finish with `N records rewritten.` and **no** "skipped". "Skipped" means some values were written with another key: add that key to the lists and run it again.

## 5. Switch to production settings

In `Backend/.env`:

```ini
DJANGO_SETTINGS_MODULE=config.settings.prod
WEB_COMMAND=gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 4 --threads 2 --worker-class gthread --timeout 120
DEBUG=False
SECRET_KEY=<new>
FIELD_ENCRYPTION_KEY=<new fernet>
FIELD_ENCRYPTION_KEY_V2=<new V2>
FIELD_HASH_KEY=<new hash key>
PAYLOAD_ENCRYPTION_ENABLED=True          # request/response bodies encrypted on top of HTTPS
POSTGRES_PASSWORD=<strong password>      # and the matching DATABASE_URL, see below
# Behind an HTTPS proxy that sets X-Forwarded-Proto, leave SECURE_SSL_REDIRECT unset (True).
# Only if TLS ends somewhere that does not send that header: SECURE_SSL_REDIRECT=False
# DEMO_ACCOUNTS=1                        # only for a public demo: loads demo data and changeme123 logins
```

To change the database password of the running Postgres:

```sh
docker compose exec db psql -U postgres -c "ALTER USER postgres PASSWORD '<strong password>';"
```

Then restart:

```sh
docker compose up -d --force-recreate web celery-worker celery-beat
```

With production settings the entrypoint no longer creates or resets the `changeme123` demo and SaaS accounts. Accounts that already exist keep their password, so in the SaaS console reset `admin@hms.polynexus.in`, the `@demo-hospital.example` logins and every other account that had `changeme123`.

## 6. Check

```sh
curl -s https://hms.polynexus.in/api/v1/__nope__/ | head -c 200   # JSON 404, no "DEBUG = True"
docker compose logs web | tail -20                                  # gunicorn, no RuntimeError about keys
ss -ltnp | grep -E ':(5435|6381) '                                  # 127.0.0.1 only
```

In the browser, DevTools → Network: request and response bodies of `/api/v1/` calls read `{"enc":"gcm2$…"}`, and the app works as before.

## 7. Backups

`docker-compose.yml` on the VM has no backup service (that comes with the on-premise bundle, `deploy/backup.sh`). Schedule a nightly `pg_dump` (step 1) to storage off the VM, encrypted, for example with `openssl enc -aes-256-cbc -pbkdf2 -iter 200000`, and keep the passphrase in the password manager.
