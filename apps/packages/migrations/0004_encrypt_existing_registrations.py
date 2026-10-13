"""Encrypt existing rows after 0003_encrypt_personal_data added the columns. Separate from the
schema migration: Postgres can't build that migration's indexes on a table
updated in the same transaction."""
from django.db import migrations


SEARCH_INDEXES = {'name_search': ('name', ['patient_name']), 'mobile_hash': ('phone', ['mobile'])}


def encrypt_existing(apps, schema_editor):
    """Re-save every row so its personal fields are written encrypted (reads
    fall back to the old plaintext until then), and fill the search columns.
    One-way: to undo, restore the pre-upgrade backup."""
    from datetime import date

    from apps.core.encryption import search_index_value

    Model = apps.get_model("packages", "CampRegistration")
    for row in Model._base_manager.all().iterator(chunk_size=500):
        if isinstance(getattr(row, "date_of_birth", None), str):
            row.date_of_birth = date.fromisoformat(row.date_of_birth) if row.date_of_birth else None
        for column, (kind, sources) in SEARCH_INDEXES.items():
            setattr(row, column, search_index_value(kind, *[getattr(row, f) or "" for f in sources]))
        row.save(update_fields=['patient_name', 'mobile', 'name_search', 'mobile_hash'])


class Migration(migrations.Migration):
    dependencies = [("packages", "0003_encrypt_personal_data")]

    operations = [migrations.RunPython(encrypt_existing, migrations.RunPython.noop)]
