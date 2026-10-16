"""Re-encrypt every encrypted field under the current primary key, and rebuild
the blind indexes (phone / email / name search hashes) under the current
FIELD_HASH_KEY. Use it to rotate keys, or to move a server off the public dev
keys before switching it to config.settings.prod.

Run with the NEW key first and the OLD key(s) after it, so old values can still
be read while every value is written back under the new key:

    FIELD_ENCRYPTION_KEYS_V2=<new>,<old>  FIELD_ENCRYPTION_KEYS=<new fernet>,<old fernet> \\
    FIELD_HASH_KEY=<new>  python manage.py reencrypt_fields [--dry-run]

Afterwards keep only the new keys. A record holding a value that no configured
key can read is skipped and counted, so nothing is overwritten with garbage.
Take a database backup first.
"""
from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from apps.core.encryption import decrypt_value
from apps.core.fields import EncryptedCharField, EncryptedDateField, EncryptedJSONField, EncryptedTextField

ENCRYPTED = (EncryptedCharField, EncryptedTextField, EncryptedJSONField, EncryptedDateField)
CHUNK = 500


def _unreadable(raw) -> bool:
    """A stored token no configured key can open (decrypt_value hands it back unchanged)."""
    return isinstance(raw, str) and raw.startswith(("gcm1$", "gAAAA")) and decrypt_value(raw) == raw


def _stored_tokens(model, fields, pks):
    """The raw column values as stored (the ORM would decrypt them on read)."""
    q = connection.ops.quote_name
    cols = ", ".join(q(f.column) for f in fields)
    pk = q(model._meta.pk.column)
    with connection.cursor() as cur:
        cur.execute(f"SELECT {pk}, {cols} FROM {q(model._meta.db_table)} WHERE {pk} IN ({', '.join(['%s'] * len(pks))})", pks)
        return cur.fetchall()


class Command(BaseCommand):
    help = "Re-encrypt all encrypted fields under the primary key and rebuild blind indexes (key rotation)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Count what would be rewritten; change nothing.")

    def handle(self, *args, dry_run=False, **options):
        total = skipped = 0
        for model in apps.get_models():
            enc = [f for f in model._meta.concrete_fields if isinstance(f, ENCRYPTED)]
            if not enc:
                continue
            # blind indexes (non-editable *_hash / *_lookup / *_search) are recomputed by the model's own save()
            hashes = [f.attname for f in model._meta.concrete_fields if not f.editable and f.attname.endswith(("_hash", "_lookup", "_search"))]
            columns = [f.attname for f in enc] + hashes
            manager = model._base_manager
            pks = list(manager.order_by("pk").values_list("pk", flat=True))
            done = bad = 0
            for start in range(0, len(pks), CHUNK):
                chunk = pks[start:start + CHUNK]
                unreadable = {row[0] for row in _stored_tokens(model, enc, chunk) if any(_unreadable(v) for v in row[1:])}
                bad += len(unreadable)
                if dry_run:
                    done += len(chunk) - len(unreadable)
                    continue
                with transaction.atomic():
                    for obj in manager.filter(pk__in=[p for p in chunk if p not in unreadable]):
                        obj.save(update_fields=columns)  # writes under keys[0]; save() rebuilds the hashes
                        done += 1
            if done or bad:
                note = f", {bad} skipped (no configured key can read them)" if bad else ""
                self.stdout.write(f"{model._meta.label}: {done} {'to rewrite' if dry_run else 'rewritten'}{note}")
            total += done
            skipped += bad
        verb = "would be rewritten" if dry_run else "rewritten"
        if skipped:
            self.stdout.write(self.style.WARNING(f"{total} records {verb}; {skipped} skipped — add the key they were written with and run again."))
        else:
            self.stdout.write(self.style.SUCCESS(f"{total} records {verb}."))
