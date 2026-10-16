"""Layer-2 payload encryption end to end, the way the browser does it
(payloadCrypto.ts): unpadded base64url, ECDH + HKDF, then encrypted login.
Regression for three bugs that kept every request in plain JSON: padding
("Incorrect padding" on the handshake), the decrypted body never reaching DRF
(request._stream still held the ciphertext), and auth/login|refresh|logout
being exempt from encryption."""
import base64
import json
import os

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ec import ECDH, SECP256R1, EllipticCurvePublicKey, generate_private_key
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.test import Client, override_settings


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")  # the browser drops the padding


def unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class BrowserSession:
    def __init__(self, client):
        self.client = client
        key = generate_private_key(SECP256R1())
        pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        res = client.post("/api/v1/session-key/", {"client_public_key": b64(pub)}, content_type="application/json")
        assert res.status_code == 200, res.content
        data = res.json()
        server = EllipticCurvePublicKey.from_encoded_point(SECP256R1(), unb64(data["server_public_key"]))
        self.session_id = data["session_id"]
        self.key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"hospital-crm-payload-v1").derive(key.exchange(ECDH(), server))

    def post(self, path, body, **extra):
        nonce = os.urandom(12)
        token = "gcm2$" + b64(nonce + AESGCM(self.key).encrypt(nonce, json.dumps(body).encode(), None))
        res = self.client.post(path, {"enc": token}, content_type="application/json", HTTP_X_SESSION_ID=self.session_id, **extra)
        return res, self.read(res)

    def get(self, path, **extra):
        res = self.client.get(path, HTTP_X_SESSION_ID=self.session_id, **extra)
        return res, self.read(res)

    def read(self, res):
        wrapper = json.loads(res.content)
        assert set(wrapper) == {"enc"}, f"response was not encrypted: {res.content[:120]!r}"
        raw = unb64(wrapper["enc"][len("gcm2$"):])
        return json.loads(AESGCM(self.key).decrypt(raw[:12], raw[12:], None))


@pytest.fixture
def encrypted_client(db):
    with override_settings(PAYLOAD_ENCRYPTION_ENABLED=True):
        yield Client()  # middleware reads the flag when the client's handler loads


def test_login_and_api_calls_are_encrypted_both_ways(encrypted_client, user):
    session = BrowserSession(encrypted_client)

    res, data = session.post("/api/v1/auth/login/", {"email": user.email, "password": "testpass123"})
    assert res.status_code == 200, data
    assert "access" in data and b"testpass123" not in res.content

    res, me = session.get("/api/v1/users/me/", HTTP_AUTHORIZATION=f"Bearer {data['access']}")
    assert res.status_code == 200 and me["email"] == user.email
    assert user.email.encode() not in res.content  # nothing readable on the wire


def test_errors_are_encrypted_too(encrypted_client, user):
    session = BrowserSession(encrypted_client)
    res, data = session.post("/api/v1/auth/login/", {"email": user.email, "password": "wrong"})
    assert res.status_code in (400, 401) and data  # readable once decrypted, never on the wire


def test_unknown_session_asks_the_browser_to_rekey(encrypted_client):
    res = encrypted_client.get("/api/v1/users/me/", HTTP_X_SESSION_ID="not-a-session")
    assert res.status_code == 401 and "Encryption session" in res.json()["detail"]


def test_clients_without_a_session_still_get_plain_json(encrypted_client, user):
    # mobile app, Postman, webhooks: no X-Session-Id, no encryption
    res = encrypted_client.post("/api/v1/auth/login/", {"email": user.email, "password": "testpass123"}, content_type="application/json")
    assert res.status_code == 200 and "access" in res.json()
