import uuid

from django.db import migrations, models


def populate_lead_webhook_tokens(apps, schema_editor):
    Hospital = apps.get_model("core", "Hospital")
    for hospital in Hospital.objects.all():
        hospital.lead_webhook_token = uuid.uuid4()
        hospital.save(update_fields=["lead_webhook_token"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    """Adds Hospital.lead_webhook_token in three steps rather than a single
    AddField, because a callable default (uuid.uuid4) on a field that's
    also unique=True is evaluated once and reused for every existing row —
    Django's makemigrations questioner flags exactly this case. With 3+
    hospital rows already in the table, a single-step AddField would give
    every existing hospital the identical token and immediately violate
    the unique constraint. Add nullable -> backfill distinct values per
    row -> tighten to NOT NULL/unique instead."""

    dependencies = [
        ("core", "0003_auditlog_db_level_immutability"),
    ]

    operations = [
        migrations.AddField(
            model_name="hospital",
            name="lead_webhook_token",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(populate_lead_webhook_tokens, noop_reverse),
        migrations.AlterField(
            model_name="hospital",
            name="lead_webhook_token",
            field=models.UUIDField(
                default=uuid.uuid4,
                unique=True,
                editable=False,
                help_text="Secret used in the inbound lead-capture webhook URL (website forms, Meta/Google lead ads). Regenerate to revoke.",
            ),
        ),
    ]
