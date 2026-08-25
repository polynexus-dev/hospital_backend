"""Application-level field encryption for at-rest protection of sensitive
PII — national ID numbers (Aadhaar/PAN/Passport), insurance policy numbers,
and similar identifiers that DPDP Act / SPDI Rules treat as sensitive
personal data (see docs/SECURITY_COMPLIANCE.md, finding C2).

EncryptedTextField alone only suits columns that are never filtered,
searched, or ordered on — Fernet encryption is non-deterministic (a random
nonce per call), so encrypted values can't support equality or substring
lookups at the database layer. For a field that legitimately needs
exact-match lookup (e.g. apps.tpa.PreAuthRequest.policy_number), pair it
with compute_blind_index() below: store a second, deterministic HMAC column
alongside the encrypted one, and filter on that instead of the encrypted
value. This only supports exact match, not substring search — there's no
way to search ciphertext-equivalent data by substring without leaking
partial-match information that defeats the point of encrypting it.
"""
import hashlib
import hmac

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


def get_fernet():
    keys = getattr(settings, "FIELD_ENCRYPTION_KEYS", None) or []
    valid_keys = []
    for key in keys:
        try:
            Fernet(key)
            valid_keys.append(key)
        except Exception:
            pass

    if not valid_keys:
        dev_key = getattr(settings, "INSECURE_DEV_FIELD_ENCRYPTION_KEY", "HcAQkwPlEzycVUsf-Ya9mb16TgvfuStY6_iDDcVFON0=")
        try:
            Fernet(dev_key)
            valid_keys = [dev_key]
        except Exception as exc:
            raise ImproperlyConfigured(f"FIELD_ENCRYPTION_KEYS contains an invalid Fernet key: {exc}") from exc

    return MultiFernet([Fernet(key) for key in valid_keys])


class EncryptedTextField(models.TextField):
    """Transparently encrypts on write (get_prep_value), decrypts on read
    (from_db_value). Stored as opaque ciphertext text — never use this on a
    field that needs to support filtering, search, or ordering.

    Supports key rotation: FIELD_ENCRYPTION_KEYS is a list; new values are
    always encrypted with the first key, but decryption tries every key in
    the list, so a rotated-out key can still decrypt old rows until they're
    next re-saved.
    """

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if not value:
            return value
        return get_fernet().encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if not value:
            return value
        try:
            return get_fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            raise InvalidToken(
                "Could not decrypt an EncryptedTextField value — the stored value "
                "isn't ciphertext from any key in FIELD_ENCRYPTION_KEYS. This means "
                "either the encryption key changed without keeping the old key in "
                "the list, or this row was written before the field was migrated "
                "to encrypted storage."
            )


def compute_blind_index(value):
    """Deterministic HMAC-SHA256 of a normalized value, hex-encoded (64
    chars) — for exact-match lookup on an otherwise-encrypted field. Same
    input always produces the same output (unlike Fernet), which is exactly
    what makes this useful for search AND what makes it unsuitable for
    anything Fernet already covers: don't use this instead of
    EncryptedTextField, use it alongside it, and never expose the resulting
    hash to API consumers (it's not a secret, but it is a stable
    fingerprint of the plaintext, which shouldn't be handed out any more
    freely than the plaintext itself).

    Normalizes with strip() + upper() before hashing so lookup isn't
    sensitive to incidental whitespace or case differences in how a policy
    number was typed/entered.
    """
    key = getattr(settings, "BLIND_INDEX_KEY", None)
    if not key:
        raise ImproperlyConfigured(
            "BLIND_INDEX_KEY is not set — required to compute or look up a "
            "blind-index value. Generate one with:\n"
            '  python -c "import secrets; print(secrets.token_hex(32))"\n'
            "and set it as the BLIND_INDEX_KEY env var."
        )
    normalized = value.strip().upper()
    return hmac.new(key.encode(), normalized.encode(), hashlib.sha256).hexdigest()
