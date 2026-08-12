from datetime import timedelta

from django.utils import timezone

from apps.patients.models import Patient

from .connectors import get_his_connector
from .models import HISBillingRecord, HISVisit

DEFAULT_SYNC_WINDOW_DAYS = 2


def sync_his_data(hospital, *, since=None) -> dict:
    if since is None:
        since = timezone.now() - timedelta(days=DEFAULT_SYNC_WINDOW_DAYS)

    connector = get_his_connector()
    visits_synced, billing_synced, unmatched = 0, 0, 0

    for row in connector.fetch_visits(since=since):
        patient = Patient.objects.filter(hospital=hospital, mobile=row["patient_mobile"]).first()
        if patient is None:
            unmatched += 1
            continue
        HISVisit.objects.update_or_create(
            hospital=hospital,
            external_visit_id=row["external_visit_id"],
            defaults={
                "patient": patient,
                "visit_type": row["visit_type"],
                "visit_date": row["visit_date"],
                "department_name": row.get("department_name", ""),
                "doctor_name": row.get("doctor_name", ""),
                "raw_payload": row,
            },
        )
        visits_synced += 1

    for row in connector.fetch_billing(since=since):
        patient = Patient.objects.filter(hospital=hospital, mobile=row["patient_mobile"]).first()
        if patient is None:
            unmatched += 1
            continue
        HISBillingRecord.objects.update_or_create(
            hospital=hospital,
            external_bill_id=row["external_bill_id"],
            defaults={
                "patient": patient,
                "bill_date": row["bill_date"],
                "total_amount": row["total_amount"],
                "paid_amount": row.get("paid_amount", 0),
                "status": row["status"],
                "raw_payload": row,
            },
        )
        billing_synced += 1

    return {"visits_synced": visits_synced, "billing_synced": billing_synced, "unmatched": unmatched}
