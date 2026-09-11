"""
Application-level field encryption for patient-identifying and clinical
data (Part A #2: ABHA/Aadhaar-linked fields, phone numbers, free-text
clinical notes).

Two primitives, deliberately kept separate:

- `encrypt_value` / `decrypt_value` — authenticated, reversible encryption
  (Fernet: AES-128-CBC + HMAC) for values the application needs back in
  plaintext (a phone number to display or dial, a diagnosis to render).
  Fernet is intentionally non-deterministic (a random nonce per call), so
  the same plaintext produces different ciphertext every time it's saved —
  equality/`icontains` lookups directly against an encrypted column return
  nothing useful.

- `blind_index` — a deterministic, one-way HMAC-SHA256 of a *normalized*
  value, stored alongside the encrypted column purely so exact-match
  lookups (caller-ID matching, dedup by phone number) keep working without
  the column itself being searchable plaintext or reversible. It answers
  "is this the same value as that row" and nothing else — never treat it
  as a shorter/faster version of the plaintext.
"""
import hashlib
import hmac
import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from cryptography.fernet import Fernet, InvalidToken, MultiFernet


def __getattr__(name):
    if name in ("EncryptedCharField", "EncryptedJSONField", "EncryptedTextField"):
        from . import fields
        return getattr(fields, name)
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")



logger = logging.getLogger(__name__)

_fernet = None


def _build_fernet() -> MultiFernet:
    keys = getattr(settings, "FIELD_ENCRYPTION_KEYS", None)
    if not keys:
        key = getattr(settings, "FIELD_ENCRYPTION_KEY", None)
        keys = [key] if key else []
    if not keys:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY is not configured.")
    try:
        return MultiFernet([Fernet(k) for k in keys])
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEY(S) must be valid urlsafe-base64-encoded "
            "32-byte Fernet keys — generate one with "
            "`Fernet.generate_key()`."
        ) from exc


def _get_fernet() -> MultiFernet:
    # Module-level cache, not a settings-time singleton: tests routinely
    # override FIELD_ENCRYPTION_KEY per-case (override_settings), and a
    # cache built once at import time would silently keep encrypting under
    # the first key ever seen in the process.
    global _fernet
    if _fernet is None:
        _fernet = _build_fernet()
    return _fernet


def reset_fernet_cache() -> None:
    """Test/ops hook — call after changing FIELD_ENCRYPTION_KEY(S) at
    runtime (key rotation, override_settings) so the next encrypt/decrypt
    call picks up the new key(s)."""
    global _fernet
    _fernet = None


def encrypt_value(value):
    if value is None or value == "":
        return value
    token = _get_fernet().encrypt(str(value).encode("utf-8"))
    return token.decode("ascii")


def decrypt_value(token):
    if token is None or token == "":
        return token
    try:
        return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        # Either the wrong key, or a value that was never encrypted (a row
        # written before this column was migrated, a fixture loaded
        # straight into the DB). Surfacing the raw stored value keeps a
        # single bad/legacy row from 500ing every list view that touches
        # it — but it does mean a rotated-away key's data reads back as
        # ciphertext garbage rather than failing loudly, so this is logged
        # for someone to notice and re-encrypt.
        logger.warning("Could not decrypt an encrypted field value — returning raw stored value.")
        return token


def normalize_phone(value: str) -> str:
    """Strips everything but digits and keeps the last 10, so the same
    physical number blind-indexes identically regardless of how it was
    typed (+91 98230 12345, 09823012345, 9823012345, with spaces/dashes)."""
    if not value:
        return ""
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits[-10:] if len(digits) > 10 else digits


def blind_index(value: str) -> str:
    """Deterministic HMAC-SHA256 hex digest of `value`, keyed by
    FIELD_HASH_KEY. Callers normalize the value themselves first (see
    `normalize_phone`) — this function does no normalization on its own
    since what counts as "the same value" is field-specific."""
    if not value:
        return ""
    key = getattr(settings, "FIELD_HASH_KEY", None)
    if not key:
        raise ImproperlyConfigured("FIELD_HASH_KEY is not configured.")
    return hmac.new(key.encode("utf-8"), str(value).encode("utf-8"), hashlib.sha256).hexdigest()


# Aliases for backward compatibility with migrations
get_fernet = _get_fernet
compute_blind_index = blind_index

