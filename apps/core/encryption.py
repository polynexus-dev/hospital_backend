"""
Application-level field encryption for patient-identifying and clinical
data (Part A #2: ABHA/Aadhaar-linked fields, phone numbers, free-text
clinical notes).

Two primitives, deliberately kept separate:

- `encrypt_value` / `decrypt_value` — authenticated, reversible encryption
  for values the application needs back in plaintext (a phone number to
  display or dial, a diagnosis to render). New values are encrypted with
  AES-256-GCM, tagged with the `_GCM_PREFIX` below so they're
  distinguishable from what this module wrote before this upgrade.
  `decrypt_value` still reads those older Fernet (AES-128-CBC + HMAC)
  tokens too, and a value that was never encrypted at all (a row written
  before its column was migrated, a raw fixture) — so upgrading this
  module does not, by itself, require re-encrypting existing data; a
  row's ciphertext moves to the new scheme the next time that row is
  saved (see e.g. apps.patients.migrations.0012/0014 for the
  "AlterField, then RunPython re-save every row" pattern this relies on
  for a deliberate bulk upgrade). Both schemes are non-deterministic (a
  random nonce per call), so the same plaintext produces different
  ciphertext every time it's saved — equality/`icontains` lookups
  directly against an encrypted column return nothing useful either way.

- `blind_index` — a deterministic, one-way HMAC-SHA256 of a *normalized*
  value, stored alongside the encrypted column purely so exact-match
  lookups (caller-ID matching, dedup by phone number) keep working without
  the column itself being searchable plaintext or reversible. It answers
  "is this the same value as that row" and nothing else — never treat it
  as a shorter/faster version of the plaintext.
"""
import base64
import binascii
import hashlib
import hmac
import logging
import os

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def __getattr__(name):
    if name in ("EncryptedCharField", "EncryptedJSONField", "EncryptedTextField"):
        from . import fields
        return getattr(fields, name)
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")



logger = logging.getLogger(__name__)

# Tag prepended to every AES-256-GCM token so decrypt_value can tell it
# apart from a legacy Fernet token (which always starts with a fixed
# version byte that base64-encodes to "gAAAAA...") or genuinely
# unencrypted legacy data — "$" never appears in urlsafe-base64 output, so
# it can't collide with either.
_GCM_PREFIX = "gcm1$"
_GCM_NONCE_LEN = 12  # bytes — the standard/recommended AES-GCM nonce size

_fernet = None
_gcm_keys = None


class _DecryptionFailed(Exception):
    """Internal — raised when no configured FIELD_ENCRYPTION_KEY_V2 can
    authenticate a `_GCM_PREFIX` token, so decrypt_value can catch it
    alongside Fernet's InvalidToken with one except clause."""


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


def _build_gcm_keys() -> list[bytes]:
    keys = getattr(settings, "FIELD_ENCRYPTION_KEYS_V2", None)
    if not keys:
        key = getattr(settings, "FIELD_ENCRYPTION_KEY_V2", None)
        keys = [key] if key else []
    if not keys:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY_V2 is not configured.")
    try:
        raw_keys = [base64.urlsafe_b64decode(k) for k in keys]
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEY_V2(S) must be urlsafe-base64-encoded "
            "32-byte AES-256 keys — generate one with "
            "`base64.urlsafe_b64encode(os.urandom(32))`."
        ) from exc
    if any(len(k) != 32 for k in raw_keys):
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY_V2(S) must decode to exactly 32 bytes (AES-256).")
    return raw_keys


def _get_gcm_keys() -> list[bytes]:
    global _gcm_keys
    if _gcm_keys is None:
        _gcm_keys = _build_gcm_keys()
    return _gcm_keys


def reset_fernet_cache() -> None:
    """Test/ops hook — call after changing FIELD_ENCRYPTION_KEY(S) or
    FIELD_ENCRYPTION_KEY_V2(S) at runtime (key rotation, override_settings)
    so the next encrypt/decrypt call picks up the new key(s)."""
    global _fernet, _gcm_keys
    _fernet = None
    _gcm_keys = None


def _gcm_encrypt(plaintext: bytes) -> str:
    # Always encrypts under the first ("primary"/newest) key — any further
    # keys in FIELD_ENCRYPTION_KEYS_V2 exist only so decrypt can still read
    # data written under a key that's being rotated out.
    key = _get_gcm_keys()[0]
    nonce = os.urandom(_GCM_NONCE_LEN)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return _GCM_PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def _gcm_decrypt(token: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(token[len(_GCM_PREFIX):].encode("ascii"))
    except (ValueError, binascii.Error) as exc:
        raise _DecryptionFailed("malformed AES-256-GCM token") from exc
    nonce, ciphertext = raw[:_GCM_NONCE_LEN], raw[_GCM_NONCE_LEN:]
    for key in _get_gcm_keys():
        try:
            return AESGCM(key).decrypt(nonce, ciphertext, None).decode("utf-8")
        except InvalidTag:
            continue
    raise _DecryptionFailed("no configured FIELD_ENCRYPTION_KEY_V2 could authenticate this value")


def encrypt_value(value):
    if value is None or value == "":
        return value
    return _gcm_encrypt(str(value).encode("utf-8"))


def decrypt_value(token):
    if token is None or token == "":
        return token
    try:
        if token.startswith(_GCM_PREFIX):
            return _gcm_decrypt(token)
        return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, _DecryptionFailed, ValueError, TypeError, binascii.Error):
        # Wrong/rotated-out key under either scheme, or a value that was
        # never encrypted at all (a row written before this column was
        # migrated, a fixture loaded straight into the DB). Surfacing the
        # raw stored value keeps a single bad/legacy row from 500ing every
        # list view that touches it — but it does mean a rotated-away
        # key's data reads back as ciphertext garbage rather than failing
        # loudly, so this is logged for someone to notice and re-encrypt.
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
