"""
Application-layer payload encryption (Layer 2).

Sits above HTTPS (Layer 1) and below the existing field-level database
encryption (Layer 3 / gcm1$).

Protocol: ECDH-P256 key exchange + HKDF-SHA256 -> shared AES-256-GCM key.

The server generates one ephemeral P-256 key pair per process startup
(module-level singleton). Each browser session performs a Diffie-Hellman
handshake via POST /api/v1/session-key/:
    1. Client sends its ephemeral P-256 public key.
    2. Server derives the shared secret via ECDH, expands it to 32 bytes
       with HKDF-SHA256, and caches the AES key under a random session_id.
    3. Server returns its own public key + the session_id.
    4. Client derives the same shared AES key independently -- the key is
       never transmitted.

Subsequent requests/responses are wrapped as:
    {"enc": "gcm2$<urlsafe-base64(12-byte-nonce + ciphertext)>"}

The "gcm2$" prefix distinguishes Layer-2 payload tokens from the existing
field-level "gcm1$" tokens so decrypt logic can never confuse the two.
"""
import base64
import logging
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import (
    ECDH,
    SECP256R1,
    generate_private_key,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)

# -- Prefix -------------------------------------------------------------------
# "gcm2$" is distinct from the field-level "gcm1$" used by apps.core.encryption
# so the two layers can never be confused.
PAYLOAD_PREFIX = "gcm2$"
_NONCE_LEN = 12  # bytes -- standard AES-GCM nonce

# -- Server keypair (module-level singleton) ----------------------------------
# Regenerated on every server restart; the browser does a fresh handshake on
# each page load so this is fine -- session_ids in the cache TTL out anyway.
_server_private_key = generate_private_key(SECP256R1())
_server_public_key = _server_private_key.public_key()


def get_server_public_key_b64() -> str:
    """Return the server public P-256 key as URL-safe base64 (uncompressed
    point, X9.62 format) for transmission to the client."""
    raw = _server_public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(raw).decode("ascii")


def derive_shared_aes_key(client_public_key_b64: str) -> bytes:
    """
    Perform ECDH with the client ephemeral P-256 public key, then expand the
    shared secret to a 32-byte AES-256 key via HKDF-SHA256.

    Raises ValueError on bad / undecodable key material.
    """
    try:
        raw = base64.urlsafe_b64decode(client_public_key_b64.encode("ascii"))
        client_pub = ec.EllipticCurvePublicKey.from_encoded_point(SECP256R1(), raw)
    except Exception as exc:
        raise ValueError(f"Invalid client public key: {exc}") from exc

    shared_secret = _server_private_key.exchange(ECDH(), client_pub)

    # HKDF-SHA256: expand the raw ECDH secret to a well-distributed 32-byte key.
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,  # stateless -- ephemeral ECDH already provides uniqueness
        info=b"hospital-crm-payload-v1",
    ).derive(shared_secret)


# -- AES-256-GCM payload encrypt / decrypt ------------------------------------

def encrypt_payload(plaintext_bytes: bytes, aes_key: bytes) -> str:
    """
    Encrypt arbitrary bytes with AES-256-GCM.
    Returns a "gcm2$<urlsafe-base64(nonce || ciphertext)>" string.
    """
    nonce = os.urandom(_NONCE_LEN)
    ciphertext = AESGCM(aes_key).encrypt(nonce, plaintext_bytes, None)
    return PAYLOAD_PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_payload(token: str, aes_key: bytes) -> bytes:
    """
    Decrypt a "gcm2$..." token produced by encrypt_payload.
    Returns the original plaintext bytes.
    Raises ValueError on bad token or authentication failure.
    """
    if not token.startswith(PAYLOAD_PREFIX):
        raise ValueError("Not a gcm2$ payload token.")
    try:
        raw = base64.urlsafe_b64decode(token[len(PAYLOAD_PREFIX):].encode("ascii"))
    except Exception as exc:
        raise ValueError(f"Malformed gcm2$ token: {exc}") from exc

    nonce, ciphertext = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
    try:
        return AESGCM(aes_key).decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise ValueError(f"AES-GCM authentication failed: {exc}") from exc
