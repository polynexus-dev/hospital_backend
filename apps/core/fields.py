"""Model fields that transparently encrypt their value at rest (Part A #2).

Important: none of these are filterable/searchable at the DB level.
Fernet encryption is non-deterministic, so `SomeModel.objects.filter(x="y")`
or a `SearchFilter`/`icontains` lookup against an encrypted column will
silently match nothing rather than error. Any field that also needs
exact-match lookup (phone number caller-ID matching, dedup) needs a
companion blind-index column — see apps.patients.models.Patient.mobile_hash
and apps.core.encryption.blind_index.
"""
import json

from django.db import models

from .encryption import decrypt_value, encrypt_value


class EncryptedTextField(models.TextField):
    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return encrypt_value(value)

    def from_db_value(self, value, expression, connection):
        return decrypt_value(value)


class EncryptedCharField(models.CharField):
    """Behaves like CharField for validation (max_length applies to the
    plaintext a caller passes in) but is stored as unbounded text — Fernet
    ciphertext is always considerably longer than the plaintext it wraps,
    so a DB column sized to the plaintext's max_length would truncate it."""

    def db_type(self, connection):
        return "text"

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return encrypt_value(value)

    def from_db_value(self, value, expression, connection):
        return decrypt_value(value)


class EncryptedJSONField(models.JSONField):
    """A JSONField whose serialized form is encrypted before it hits the
    DB. Stored as plain text, not jsonb/json — once encrypted the column
    value isn't valid JSON any more, so no native JSON column type applies
    and no JSON-path querying is possible."""

    def db_type(self, connection):
        return "text"

    def get_db_prep_value(self, value, connection, prepared=False):
        # Encrypt the JSON text and store the ciphertext as-is. The previous
        # get_prep_value override ran *before* JSONField's own adaptation,
        # so it encrypted Python's str() repr of the value and Django then
        # JSON-quoted the ciphertext — which from_db_value could no longer
        # recognise, so reads silently returned the raw ciphertext string.
        # Stored as a JSON *string* holding the ciphertext, so the column
        # still satisfies JSONField's JSON_VALID check constraint.
        if value is None:
            return None
        return json.dumps(encrypt_value(json.dumps(value, cls=self.encoder)))

    def from_db_value(self, value, expression, connection):
        if value in (None, ""):
            return value
        if value.startswith('"'):
            # JSON string wrapping the ciphertext (current and legacy rows).
            try:
                value = json.loads(value)
            except ValueError:
                pass
        value = decrypt_value(value)
        if value in (None, ""):
            return value
        try:
            return json.loads(value, cls=self.decoder) if self.decoder else json.loads(value)
        except ValueError:
            # Legacy plaintext was Python's repr (single quotes), not JSON.
            import ast

            try:
                return ast.literal_eval(value)
            except (ValueError, SyntaxError):
                return value
