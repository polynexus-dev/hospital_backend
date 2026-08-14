"""Data migration: encrypt any existing plaintext national_id_number /
insurance_policy_number values in place before migration 0008 switches the
model field to EncryptedTextField and starts decrypting on every read.

Must run strictly between 0006 (column widened to text, still plaintext)
and 0008 (field class swapped to EncryptedTextField) — reading a
not-yet-encrypted row through EncryptedTextField.from_db_value() raises
InvalidToken.
"""
from django.db import migrations

from apps.core.encryption import get_fernet

ENCRYPTED_FIELDS = ["national_id_number", "insurance_policy_number"]


def encrypt_existing_values(apps, schema_editor):
    Patient = apps.get_model("patients", "Patient")
    fernet = get_fernet()
    for patient in Patient.objects.all().iterator():
        updates = {
            field: fernet.encrypt(getattr(patient, field).encode()).decode()
            for field in ENCRYPTED_FIELDS
            if getattr(patient, field)
        }
        if updates:
            for field, value in updates.items():
                setattr(patient, field, value)
            patient.save(update_fields=list(updates.keys()))


def decrypt_existing_values(apps, schema_editor):
    """Reverse: best-effort — only decrypts rows whose ciphertext is valid
    under a currently-configured FIELD_ENCRYPTION_KEYS key. A row encrypted
    under a key that's since been rotated out can't be reversed here."""
    from cryptography.fernet import InvalidToken

    Patient = apps.get_model("patients", "Patient")
    fernet = get_fernet()
    for patient in Patient.objects.all().iterator():
        updates = {}
        for field in ENCRYPTED_FIELDS:
            value = getattr(patient, field)
            if not value:
                continue
            try:
                updates[field] = fernet.decrypt(value.encode()).decode()
            except InvalidToken:
                continue
        if updates:
            for field, value in updates.items():
                setattr(patient, field, value)
            patient.save(update_fields=list(updates.keys()))


class Migration(migrations.Migration):

    dependencies = [
        ("patients", "0006_widen_national_id_and_insurance_fields"),
    ]

    operations = [
        migrations.RunPython(encrypt_existing_values, decrypt_existing_values),
    ]
