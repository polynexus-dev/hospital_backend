# Production backend image: multi-stage, slim, non-root, gunicorn,
# collectstatic at build time.
#
#   docker build -t hms-backend:<version> .                          on-premise (compiled, licence enforced)
#   docker build --build-arg COMPILE=0 --build-arg ON_PREMISE=0 .    SaaS
#
# COMPILE=1 compiles apps/ and config/ to native modules with Cython (no .py
# source in the image). ON_PREMISE=1 fixes the image to on-premise mode
# (config/build_info.py) so an environment variable can't switch licensing off.
# An on-premise build needs the licence public key: run
# `python manage.py generate_license_keypair` once (platform side).

ARG PYTHON_IMAGE=python:3.12-slim

# ---- build -----------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS build
ARG COMPILE=1
ARG ON_PREMISE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1

WORKDIR /src
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN if [ "$ON_PREMISE" = "1" ]; then \
        grep -q 'PUBLIC_KEY_PEM = """' apps/licensing/public_key.py \
          || (echo "apps/licensing/public_key.py has no key: run generate_license_keypair first." >&2; exit 1); \
    fi

# The finished application goes to /build; /src (the source) is never stripped.
RUN if [ "$COMPILE" = "1" ]; then \
        pip install --no-cache-dir "cython>=3.0,<4" setuptools \
        && python scripts/compile_cython.py --out /build --jobs "$(nproc)" $( [ "$ON_PREMISE" = "1" ] && echo --on-premise ) \
        && pip uninstall -y cython; \
    else \
        mkdir /build && cp -r /src/. /build/ \
        && rm -rf /build/tools /build/docs /build/deploy /build/venv /build/.git \
        && if [ "$ON_PREMISE" = "1" ]; then printf 'FORCED_DEPLOYMENT_MODE = "on_premise"\n' > /build/config/build_info.py; fi; \
    fi \
    && cd /build \
    && DJANGO_SETTINGS_MODULE=config.settings.dev CACHE_URL=locmem:// python manage.py collectstatic --noinput \
    && rm -rf Dockerfile* docker-compose.yml docker-entrypoint.sh *.md *.json requirements-dev.txt Makefile

# ---- runtime ---------------------------------------------------------------
FROM ${PYTHON_IMAGE}

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home app \
    && mkdir -p /app/config /app/media && chown -R app:app /app

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod \
    LICENSE_FILE_PATH=/app/config/license.lic \
    MEDIA_ROOT=/app/media

COPY --from=build /opt/venv /opt/venv
COPY --from=build --chown=app:app /build /opt/hms
WORKDIR /opt/hms
RUN chmod +x docker-entrypoint.onprem.sh

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health/', timeout=4).status == 200 else 1)"
ENTRYPOINT ["/opt/hms/docker-entrypoint.onprem.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
