"""License files: Ed25519-signed JSON payloads in a base64 envelope.

A `.lic` file is base64 of a small JSON envelope:

    {"v": 1, "nonce": ..., "body": ..., "sig": ...}

`body` is the canonical payload JSON, AES-GCM encrypted with a key derived
from the public key, so the file isn't casually readable or editable. That
key ships with every installation, so this is obfuscation only — what makes
a license trustworthy is `sig`, an Ed25519 signature over the plaintext
payload that only the platform's private key can produce.
"""
import base64
import hashlib
import json
import os

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENVELOPE_VERSION = 1


class LicenseTampered(Exception):
    """Unreadable, altered, or not signed by the platform."""


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def load_private_key(pem: str) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("The license signing key must be an Ed25519 private key.")
    return key


def load_public_key(pem: str) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(pem.encode())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("The license public key must be an Ed25519 public key.")
    return key


def public_pem(private_key: Ed25519PrivateKey) -> str:
    return private_key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def private_pem(private_key: Ed25519PrivateKey) -> str:
    return private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


def _envelope_key(public_key: Ed25519PublicKey) -> bytes:
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(b"hms-license-envelope:" + raw).digest()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def seal(payload: dict, private_key: Ed25519PrivateKey) -> str:
    """The `.lic` file contents for `payload`."""
    body = canonical(payload)
    nonce = os.urandom(12)
    envelope = {
        "v": ENVELOPE_VERSION,
        "nonce": _b64(nonce),
        "body": _b64(AESGCM(_envelope_key(private_key.public_key())).encrypt(nonce, body, None)),
        "sig": _b64(private_key.sign(body)),
    }
    return _b64(json.dumps(envelope, separators=(",", ":")).encode())


def open_license(blob: str, public_key: Ed25519PublicKey) -> dict:
    """The verified payload of a `.lic` file. Raises LicenseTampered."""
    try:
        envelope = json.loads(base64.b64decode("".join(blob.split()), validate=True))
        if envelope.get("v") != ENVELOPE_VERSION:
            raise LicenseTampered("Unsupported license file version.")
        body = AESGCM(_envelope_key(public_key)).decrypt(base64.b64decode(envelope["nonce"]), base64.b64decode(envelope["body"]), None)
        public_key.verify(base64.b64decode(envelope["sig"]), body)
        payload = json.loads(body)
    except LicenseTampered:
        raise
    except (InvalidSignature, InvalidTag):
        raise LicenseTampered("The license signature is not valid.")
    except (ValueError, KeyError, TypeError):
        raise LicenseTampered("The license file is unreadable.")
    if not isinstance(payload, dict):
        raise LicenseTampered("The license file is unreadable.")
    return payload
