"""Helpers for data migrations that move existing rows into encrypted fields."""
from django.db.migrations import RunPython


def encrypt_existing(app_label, fields_by_model):
    """RunPython that rewrites existing rows after an AlterField to an Encrypted*Field.

    Reading through the (historical) encrypted field returns plaintext as is
    (decrypt_value passes non-token values through), and QuerySet.update
    writes it back through get_prep_value, i.e. encrypted. Safe to run twice.
    The reverse writes the plaintext back with raw SQL, so rolling back never
    leaves ciphertext in a plain column.
    """

    def forward(apps, schema_editor):
        for model_name, fields in fields_by_model.items():
            model = apps.get_model(app_label, model_name)
            manager = model._base_manager
            for obj in manager.only("pk", *fields).iterator(chunk_size=500):
                manager.filter(pk=obj.pk).update(**{f: getattr(obj, f) for f in fields})

    def reverse(apps, schema_editor):
        connection = schema_editor.connection
        q = connection.ops.quote_name
        for model_name, fields in fields_by_model.items():
            model = apps.get_model(app_label, model_name)
            cols = [model._meta.get_field(f).column for f in fields]
            sql = f"UPDATE {q(model._meta.db_table)} SET {', '.join(f'{q(c)} = %s' for c in cols)} WHERE {q(model._meta.pk.column)} = %s"
            with connection.cursor() as cur:
                for obj in model._base_manager.only("pk", *fields).iterator(chunk_size=500):
                    cur.execute(sql, [getattr(obj, f) for f in fields] + [obj.pk])

    return RunPython(forward, reverse)
