"""Fill first_contacted_at from each enquiry's stage history (the first move
out of New). Existing New enquiries keep no deadline, so the first escalation
run after the upgrade doesn't raise tasks for old leads; the ageing report
still shows them."""
from django.db import migrations
from django.db.models import Min


def backfill(apps, schema_editor):
    Enquiry = apps.get_model("enquiries", "Enquiry")
    StageChange = apps.get_model("enquiries", "EnquiryStageChange")
    firsts = StageChange.objects.filter(from_stage="new").values("enquiry_id").annotate(at=Min("created_at"))
    for row in firsts.iterator():
        Enquiry.objects.filter(pk=row["enquiry_id"], first_contacted_at__isnull=True).update(first_contacted_at=row["at"])


class Migration(migrations.Migration):
    dependencies = [("enquiries", "0013_sla_tracking")]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
