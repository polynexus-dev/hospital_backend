from celery import shared_task
from django.utils import timezone


@shared_task
def refresh_pin_directory(run_id):
    """Fetch the PIN directory from data.gov.in and add anything new."""
    from django.core.management import call_command

    from .models import PinCode, PinDirectoryRefresh

    run = PinDirectoryRefresh.objects.get(pk=run_id)
    try:
        call_command("import_pincodes", "--from-api")
    except Exception as exc:  # noqa: BLE001 — recorded for the admin who pressed the button
        run.error = str(exc)[:2000]
    run.offices_after = PinCode.objects.count()
    run.pincodes_after = PinCode.objects.values("pincode").distinct().count()
    run.finished_at = timezone.now()
    run.save()
