"""Layer-2 payload encryption as a hard requirement: every way around it is
tried here, with a real ECDH handshake against the real middleware."""
import base64
import json
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import get_resolver, URLPattern, URLResolver
from rest_framework.permissions import AllowAny
from rest_framework.test import APIClient

from apps.core import middleware as mw
from apps.core.payload_crypto import decrypt_payload, encrypt_payload
from apps.patients.models import Document, Patient


class Session:
    """What a client does: handshake, then seal and open."""

    def __init__(self, client):
        key = ec.generate_private_key(ec.SECP256R1())
        public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        response = client.post("/api/v1/session-key/", {"client_public_key": base64.urlsafe_b64encode(public).decode()}, format="json")
        assert response.status_code == 200
        self.id = response.data["session_id"]
        raw = response.data["server_public_key"]
        server = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        self.key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"hospital-crm-payload-v1").derive(key.exchange(ec.ECDH(), server))

    def seal(self, data):
        return encrypt_payload(data if isinstance(data, bytes) else json.dumps(data).encode(), self.key)

    def headers(self, query=None):
        out = {"HTTP_X_SESSION_ID": self.id}
        if query:
            out["HTTP_X_ENC_QUERY"] = self.seal(query.encode())
        return out

    def open(self, response):
        return decrypt_payload(json.loads(response.content)["enc"], self.key)


@pytest.fixture
def enforced(settings):
    settings.PAYLOAD_ENCRYPTION_ENABLED = True
    settings.PUBLIC_APP_URL = "https://apollo.hms.polynexus.in"


@pytest.fixture
def client(enforced, user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


@pytest.fixture
def session(client):
    return Session(client)


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Asha", last_name="Patil", mobile="9822011111")


@pytest.mark.django_db
def test_a_call_without_a_session_is_refused_before_any_view(client, patient):
    for method, kwargs in (("get", {}), ("post", {"data": {"first_name": "X"}, "format": "json"})):
        response = getattr(client, method)("/api/v1/patients/", **kwargs)
        assert response.status_code == 401
        assert "Encryption session" in response.json()["detail"]
    assert client.post("/api/v1/mobile/auth/otp/send/", {"mobile": "9822011111"}, format="json").status_code == 401  # mobile too
    assert client.post("/api/v1/portal/auth/request-otp/", {}, format="json").status_code == 401  # portal too
    assert client.post("/api/v1/auth/login/", {"email": "a@b.in", "password": "x"}, format="json").status_code == 401  # passwords too


@pytest.mark.django_db
def test_a_made_up_session_is_refused(client):
    assert client.get("/api/v1/patients/", HTTP_X_SESSION_ID="not-a-session").status_code == 401


@pytest.mark.django_db
def test_a_plain_body_with_a_valid_session_is_refused(client, session):
    response = client.post("/api/v1/patients/", {"first_name": "Plain", "mobile": "9000000000"}, format="json", **session.headers())
    assert response.status_code == 400
    assert not Patient.objects.filter(mobile_hash__isnull=False).exists()


@pytest.mark.django_db
def test_a_plain_multipart_upload_with_a_valid_session_is_refused(client, session, patient):
    upload = SimpleUploadedFile("x.pdf", b"%PDF", content_type="application/pdf")
    response = client.post("/api/v1/documents/", {"patient": patient.pk, "file": upload}, format="multipart", **session.headers())
    assert response.status_code == 400
    assert not Document.objects.exists()


@pytest.mark.django_db
def test_a_plain_query_string_is_refused(client, session):
    assert client.get("/api/v1/patients/?search=Asha", **session.headers()).status_code == 400


@pytest.mark.django_db
def test_a_tampered_or_foreign_token_is_refused(client, session):
    other = Session(client)
    body = json.dumps({"enc": other.seal({"first_name": "X"})})
    assert client.post("/api/v1/patients/", body, content_type="application/json", **session.headers()).status_code == 400


@pytest.mark.django_db
def test_a_captured_request_can_not_be_replayed(client, session, patient):
    body = json.dumps({"enc": session.seal({"mobile": "9000000000"})})
    url = f"/api/v1/patients/{patient.pk}/"
    assert client.patch(url, body, content_type="application/json", **session.headers()).status_code == 200
    replay = client.patch(url, body, content_type="application/json", **session.headers())
    assert replay.status_code == 400 and "already used" in replay.json()["detail"]


@pytest.mark.django_db
def test_encrypted_json_and_query_work_and_the_response_is_encrypted(client, session, patient):
    response = client.get("/api/v1/patients/", **session.headers(query=f"mobile={patient.mobile}&page=1"))
    assert response.status_code == 200
    assert b"Asha" not in response.content
    assert json.loads(session.open(response))["count"] >= 1


@pytest.mark.django_db
def test_encrypted_upload_and_encrypted_download(client, session, patient, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    boundary = "XBOUNDARYX"
    parts = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="patient"\r\n\r\n{patient.pk}\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="category"\r\n\r\nreport\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="scan.pdf"\r\nContent-Type: application/pdf\r\n\r\n'
    ).encode() + b"%PDF-secret-scan" + f"\r\n--{boundary}--\r\n".encode()
    wrapper = {"enc": session.seal(parts), "ct": f"multipart/form-data; boundary={boundary}"}

    created = client.post("/api/v1/documents/", json.dumps(wrapper), content_type="application/json", **session.headers())
    assert created.status_code == 201, session.open(created)
    doc = json.loads(session.open(created))

    response = client.get(f"/api/v1/documents/{doc['id']}/file/", **session.headers())
    assert response.status_code == 200
    assert b"secret-scan" not in response.content
    assert "Content-Disposition" not in response  # the file name stays out too
    sealed = json.loads(response.content)
    assert sealed["ct"].startswith("application/pdf")
    assert decrypt_payload(sealed["enc"], session.key) == b"%PDF-secret-scan"


@pytest.mark.django_db
def test_an_upload_larger_than_the_django_memory_limit_passes(client, session, patient, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 1024
    boundary = "XB"
    parts = (f'--{boundary}\r\nContent-Disposition: form-data; name="patient"\r\n\r\n{patient.pk}\r\n'
             f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="big.pdf"\r\nContent-Type: application/pdf\r\n\r\n').encode() + b"x" * 8192 + f"\r\n--{boundary}--\r\n".encode()
    body = json.dumps({"enc": session.seal(parts), "ct": f"multipart/form-data; boundary={boundary}"})
    assert client.post("/api/v1/documents/", body, content_type="application/json", **session.headers()).status_code == 201


@pytest.mark.django_db
def test_oversized_bodies_are_refused(client, session, settings):
    settings.PAYLOAD_MAX_BODY_BYTES = 10
    body = json.dumps({"enc": session.seal({"a": "b" * 100})})
    assert client.post("/api/v1/patients/", body, content_type="application/json", **session.headers()).status_code == 413


@pytest.mark.django_db
def test_error_responses_are_encrypted_too(client, session):
    response = client.get("/api/v1/patients/999999/", **session.headers())
    assert response.status_code == 404
    assert "detail" in json.loads(session.open(response))
    assert set(json.loads(response.content)) == {"enc"}


@pytest.mark.django_db
def test_a_response_that_can_not_be_encrypted_is_never_sent_plain(client, session, patient):
    with patch("apps.core.middleware.encrypt_payload", side_effect=RuntimeError("boom")):
        response = client.get("/api/v1/patients/", **session.headers())
    assert response.status_code == 500
    assert b"Asha" not in response.content


@pytest.mark.django_db
def test_refresh_cookie_survives_sealing(enforced, hospital, department):
    from django.conf import settings as dj

    from apps.accounts.models import User

    User.objects.create_user(email="c@test-hospital.example", password="correct-horse-1", hospital=hospital, department=department)
    c = APIClient()
    s = Session(c)
    body = json.dumps({"enc": s.seal({"email": "c@test-hospital.example", "password": "correct-horse-1"})})
    response = c.post("/api/v1/auth/login/", body, content_type="application/json", HTTP_X_AUTH_COOKIE="1", **s.headers())
    assert response.status_code == 200
    assert response.cookies[dj.REFRESH_COOKIE_NAME].value
    assert "access" in json.loads(s.open(response))


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/api/health/", "/api/v1/webhooks/msg91/abc/", "/api/v1/leads/meta/00000000-0000-0000-0000-000000000000/",
                                  "/api/v1/abdm/data-push/", "/api/v1/site/key/config/", "/api/v1/auth/sso/callback/"])
def test_machine_and_redirect_callers_are_not_asked_for_a_session(enforced, path):
    client = APIClient()
    response = client.get(path) if "health" in path or "sso" in path or "site" in path else client.post(path, {}, format="json")
    assert not (response.status_code == 401 and "Encryption session" in response.content.decode())


@pytest.mark.django_db
def test_nothing_can_slip_onto_the_allow_list_by_prefix(enforced):
    for path in ("/api/v1/patients/", "/api/v1/sitemap/", "/api/v1/leads-export/", "/api/v1/abdm/consent-requests/", "/api/v1/auth/sso/exchange/",
                 "/api/v1/auth/sso/providers/", "/api/v1/mobile-upload/x/", "/api/v1/telemedicine/join/x/"):
        assert not mw._PLAINTEXT_ALLOWED.match(path) or path.startswith("/api/v1/site/"), path


def _walk(patterns, prefix=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            yield from _walk(p.url_patterns, prefix + str(p.pattern))
        elif isinstance(p, URLPattern):
            yield prefix + str(p.pattern), p.callback


# Public, but always called by the app through the encrypted client.
ENCRYPTED_PUBLIC = (
    "api/v1/public/", "api/v1/auth/", "api/v1/mobile-upload/", "api/v1/registration/", "api/v1/feedback/", "api/v1/bloodbank/public/",
    "api/v1/governance/public/", "api/v1/queue/public/", "api/v1/telemedicine/join/", "api/v1/portal/auth/", "api/v1/mobile/auth/",
)


def test_every_public_endpoint_is_accounted_for():
    """A new AllowAny view must be a deliberate decision: either it is on the
    allow-list with a reason, or the app reaches it encrypted."""
    for url, callback in _walk(get_resolver().url_patterns):
        cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
        if cls is None or AllowAny not in (getattr(cls, "permission_classes", None) or []) or not url.startswith("api"):
            continue
        path = "/" + url.replace("^", "").replace("<int:pk>", "1")
        assert mw._PLAINTEXT_ALLOWED.match(path) or url.startswith(ENCRYPTED_PUBLIC), f"{url}: decide how this is encrypted"


@pytest.mark.django_db
def test_links_in_patient_messages_redirect_instead_of_serving_the_file(enforced):
    client = APIClient()
    for url, target in (("/api/v1/laboratory/report-link/TOK/", "/r/lab/TOK"), ("/api/v1/finance/payout-statement/TOK/", "/r/statement/TOK")):
        response = client.get(url)
        assert response.status_code == 302 and response["Location"].endswith(target)


def test_production_refuses_to_start_with_encryption_off():
    from apps.core.test_security_hardening import _load_prod_settings

    result = _load_prod_settings(DATABASE_URL="postgres://u:p@postgres:5432/hms", PAYLOAD_ENCRYPTION_ENABLED="False")
    assert result.returncode != 0 and "PAYLOAD_ENCRYPTION_ENABLED" in result.stderr


def test_production_has_encryption_on_by_default():
    import os
    import subprocess
    import sys

    from apps.core.test_security_hardening import BACKEND, PROD_ENV

    env = {**os.environ, **PROD_ENV, "DATABASE_URL": "postgres://u:p@postgres:5432/hms"}
    env.pop("USE_SQLITE", None)
    env.pop("PAYLOAD_ENCRYPTION_ENABLED", None)
    out = subprocess.run([sys.executable, "-c", "import config.settings.prod as s; print(s.PAYLOAD_ENCRYPTION_ENABLED)"], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert out.stdout.strip() == "True", out.stderr


@pytest.fixture(autouse=True)
def _clean_seen_tokens():
    yield
    cache.clear()
