"""WASA hardening: database TLS in production, PII-free error monitoring,
verified TLS to the PBX."""
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from apps.core.monitoring import scrub_event

BACKEND = Path(__file__).resolve().parents[2]
# Non-placeholder values so prod.py's own key guards pass and only the
# database check is exercised.
PROD_ENV = {
    "SECRET_KEY": "-*kh7_(=_4aq0*f)1h65wncn$r%9$je&djl1j4fp^=^8ykc33o",
    "FIELD_ENCRYPTION_KEY": "QaTZ1SVvHlAxYApyGGsKYHgDP0gHz9jY6tcLMrd7iA4=",
    "FIELD_ENCRYPTION_KEY_V2": "d7Ka8csLrq4GSCfK1xrP_uGbg3x6tTinX-K6jqT6vEw=",
    "FIELD_HASH_KEY": "O31bU4pjbzkB3djMs3VjDqFxFLHgvt6Ajk-irCG7egQ",
}


def _load_prod_settings(**env):
    full = {**os.environ, **PROD_ENV, **env}
    full.pop("USE_SQLITE", None)
    if "DB_SSLMODE" not in env:
        full.pop("DB_SSLMODE", None)
    return subprocess.run(
        [sys.executable, "-c", "import config.settings.prod as s; print(s.DATABASES['default'].get('OPTIONS', {}).get('sslmode', ''))"],
        cwd=BACKEND, env=full, capture_output=True, text=True, timeout=120,
    )


@pytest.mark.parametrize("url", [
    "postgres://u:p@postgres:5432/hms",  # compose service name
    "postgres://u:p@localhost:5432/hms",
    "postgres://u:p@127.0.0.1:5432/hms",
])
def test_prod_allows_a_database_on_this_host_without_tls(url):
    result = _load_prod_settings(DATABASE_URL=url)
    assert result.returncode == 0, result.stderr


def test_prod_refuses_a_remote_database_without_tls():
    result = _load_prod_settings(DATABASE_URL="postgres://u:p@db.internal.example.in:5432/hms")
    assert result.returncode != 0
    assert "without TLS" in result.stderr


@pytest.mark.parametrize("env", [
    {"DATABASE_URL": "postgres://u:p@10.0.0.5:5432/hms", "DB_SSLMODE": "require"},
    {"DATABASE_URL": "postgres://u:p@10.0.0.5:5432/hms?sslmode=verify-full"},
])
def test_prod_accepts_a_remote_database_over_tls(env):
    result = _load_prod_settings(**env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() in ("require", "verify-full")


def test_error_events_are_stripped_of_patient_data():
    event = {
        "request": {
            "url": "https://apollo.hms.polynexus.in/api/v1/patients/?search=Asha",
            "data": {"first_name": "Asha", "mobile": "9800000000"},
            "cookies": {"hms_refresh": "secret"},
            "query_string": "search=Asha",
            "headers": {"Authorization": "Bearer abc", "User-Agent": "Chrome", "X-Session-Id": "s"},
            "env": {"REMOTE_ADDR": "1.2.3.4"},
        },
        "user": {"id": 7, "email": "doc@example.in", "ip_address": "1.2.3.4"},
    }

    scrubbed = scrub_event(event)

    assert scrubbed["request"] == {"url": "https://apollo.hms.polynexus.in/api/v1/patients/", "headers": {"User-Agent": "Chrome"}}
    assert scrubbed["user"] == {"id": 7}


@pytest.mark.parametrize("ca_bundle, expected", [("", True), ("/etc/ssl/pbx-ca.pem", "/etc/ssl/pbx-ca.pem")])
def test_pbx_calls_always_verify_tls(settings, ca_bundle, expected):
    from apps.telephony.adapters import HoduPBXProvider

    settings.HODUPBX_BASE_URL = "https://pbx.example.in/api"
    settings.HODUPBX_TOKEN_ID = "tok"
    settings.HODUPBX_CA_BUNDLE = ca_bundle
    provider = HoduPBXProvider()
    with mock.patch("requests.post") as post:  # imported inside the method
        post.return_value.json.return_value = {"status": "SUCCESS", "data": "uuid-1"}
        provider.initiate_call(from_number="101", to_number="9800000000")
    assert post.call_args.kwargs["verify"] == expected
