"""Encrypt existing rows after 0016_encrypt_name_dob_email added the columns. Separate from the
schema migration: Postgres can't build that migration's indexes on a table
updated in the same transaction."""
from django.db import migrations


def encrypt_existing(apps, schema_editor):
    """Re-save every patient so name, date of birth and email are written
    encrypted (reads fall back to the old plaintext until then), and fill
    the search columns. One-way: to undo, restore the pre-upgrade backup."""
    from datetime import date

    from apps.core.encryption import email_index, name_search_tokens

    Patient = apps.get_model("patients", "Patient")
    for patient in Patient._base_manager.all().iterator(chunk_size=500):
        dob = patient.date_of_birth
        if isinstance(dob, str):
            dob = date.fromisoformat(dob) if dob else None
        patient.date_of_birth = dob
        patient.name_search = name_search_tokens(patient.first_name, patient.last_name)
        patient.email_hash = email_index(patient.email)
        patient.birth_year = dob.year if dob else None
        patient.save(update_fields=["first_name", "last_name", "date_of_birth", "email", "name_search", "email_hash", "birth_year"])


class Migration(migrations.Migration):
    dependencies = [("patients", "0017_registration_details")]

    operations = [migrations.RunPython(encrypt_existing, migrations.RunPython.noop)]
