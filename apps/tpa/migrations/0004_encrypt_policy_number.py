"""Data migration: encrypt any existing plaintext policy_number values and
backfill policy_number_lookup (the blind-index column PreAuthRequestViewSet
uses for exact-match search) before migration 0005 switches the model
field to EncryptedTextField and starts decrypting on every read.

Must run strictly between 0003 (column widened, lookup column added, still
plaintext) and 0005 (field class swapped to EncryptedTextField) — reading a
not-yet-encrypted row through EncryptedTextField.from_db_value() raises
InvalidToken.
"""
from django.db import migrations

from apps.core.encryption import compute_blind_index, get_fernet


def encrypt_existing_values(apps, schema_editor):
    PreAuthRequest = apps.get_model("tpa", "PreAuthRequest")
    fernet = get_fernet()
    for request in PreAuthRequest.objects.all().iterator():
        if not request.policy_number:
            continue
        request.policy_number_lookup = compute_blind_index(request.policy_number)
        request.policy_number = fernet.encrypt(request.policy_number.encode()).decode()
        request.save(update_fields=["policy_number", "policy_number_lookup"])


def decrypt_existing_values(apps, schema_editor):
    """Reverse: best-effort — only decrypts rows whose ciphertext is valid
    under a currently-configured FIELD_ENCRYPTION_KEYS key."""
    from cryptography.fernet import InvalidToken

    PreAuthRequest = apps.get_model("tpa", "PreAuthRequest")
    fernet = get_fernet()
    for request in PreAuthRequest.objects.all().iterator():
        if not request.policy_number:
            continue
        try:
            request.policy_number = fernet.decrypt(request.policy_number.encode()).decode()
        except InvalidToken:
            continue
        request.policy_number_lookup = ""
        request.save(update_fields=["policy_number", "policy_number_lookup"])


class Migration(migrations.Migration):

    dependencies = [
        ("tpa", "0003_widen_and_add_policy_number_lookup"),
    ]

    operations = [
        migrations.RunPython(encrypt_existing_values, decrypt_existing_values),
    ]
