FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN SECRET_KEY=build-time-dummy-secret-key-for-collectstatic-only \
    FIELD_ENCRYPTION_KEYS=gAAAAABnK8xL9pQ2vR4sT6uV8wX0yZ2aB4cD6eF8gH0iJ2kL4mN6oP8= \
    BLIND_INDEX_KEY=a1b2c3d4e5f67890123456789abcdef0123456789abcdef0123456789abcdef0 \
    python manage.py collectstatic --noinput --settings=config.settings.prod || true

RUN groupadd --system app && useradd --system --gid app --home /app app \
    && chown -R app:app /app
USER app

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
