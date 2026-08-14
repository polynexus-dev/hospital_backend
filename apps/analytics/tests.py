import datetime
from contextlib import suppress
from decimal import Decimal

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.analytics.models import DailyMISLog
from apps.appointments.models import Appointment, Doctor, Slot
from apps.appointments.services import book_appointment, mark_no_show
from apps.automation.models import Task
from apps.enquiries.models import Enquiry
from apps.integrations.models import HISBillingRecord
from apps.patients.models import Patient
from apps.telephony.models import Call
from django.contrib.contenttypes.models import ContentType


def _teardown(instance):
    """Same best-effort explicit-teardown pattern as Backend/conftest.py —
    the transaction rollback pytest-django performs is the real isolation
    guarantee, this is just for visible symmetry with the fixtures there."""
    with suppress(Exception):
        instance.delete()


@pytest.fixture
def doctor(hospital, department):
    created = Doctor.objects.create(hospital=hospital, department=department, name="Mehta", speciality="Cardiology")
    yield created
    _teardown(created)


@pytest.fixture
def patient(hospital):
    created = Patient.objects.create(hospital=hospital, first_name="Asha", mobile="9822000001")
    yield created
    _teardown(created)


@pytest.fixture
def slot(hospital, doctor):
    created = Slot.objects.create(
        hospital=hospital, doctor=doctor,
        date=timezone.localdate(), start_time=datetime.time(10, 0), end_time=datetime.time(10, 15),
    )
    yield created
    _teardown(created)


# --- DailyMISLog / DailyMISLogViewSet -----------------------------------
#
# One of the six viewsets the parallel audit confirmed was ALREADY safely
# tenant-scoped (explicit `filter(hospital_id=...)` in get_queryset, not
# routed through the vulnerable TenantScopedViewSetMixin pattern). No write
# endpoint exists anywhere for this model — rows are only ever produced by
# the Celery MIS task — so every row here is seeded directly via the ORM.


@pytest.mark.django_db
def test_mis_log_list_returns_only_the_authenticated_users_hospital(auth_client, hospital, other_hospital):
    mine = DailyMISLog.objects.create(hospital=hospital, report_date=timezone.localdate(), summary={"calls": 5})
    DailyMISLog.objects.create(hospital=other_hospital, report_date=timezone.localdate(), summary={"calls": 99})

    response = auth_client.get("/api/v1/mis-logs/")

    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert ids == {mine.id}


@pytest.mark.django_db
def test_mis_log_retrieve_404s_for_another_hospitals_log(auth_client, other_hospital):
    theirs = DailyMISLog.objects.create(hospital=other_hospital, report_date=timezone.localdate(), summary={})

    response = auth_client.get(f"/api/v1/mis-logs/{theirs.id}/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_mis_log_retrieve_returns_full_fields_for_own_hospital(auth_client, hospital):
    mine = DailyMISLog.objects.create(
        hospital=hospital, report_date=datetime.date(2026, 8, 1),
        summary={"calls": {"received": 10}}, send_error="",
    )

    response = auth_client.get(f"/api/v1/mis-logs/{mine.id}/")

    assert response.status_code == 200
    assert response.data["report_date"] == "2026-08-01"
    assert response.data["summary"] == {"calls": {"received": 10}}
    assert response.data["sent_at"] is None
    assert response.data["send_error"] == ""


@pytest.mark.django_db
def test_mis_log_list_can_filter_by_report_date(auth_client, hospital):
    DailyMISLog.objects.create(hospital=hospital, report_date=datetime.date(2026, 8, 1), summary={})
    later = DailyMISLog.objects.create(hospital=hospital, report_date=datetime.date(2026, 8, 2), summary={})

    response = auth_client.get("/api/v1/mis-logs/", {"report_date": "2026-08-02"})

    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert ids == {later.id}


@pytest.mark.django_db
def test_mis_log_unauthenticated_request_is_rejected_not_unscoped(api_client):
    response = api_client.get("/api/v1/mis-logs/")
    assert response.status_code == 401


@pytest.mark.django_db
def test_daily_mis_log_is_unique_per_hospital_per_day(hospital):
    DailyMISLog.objects.create(hospital=hospital, report_date=datetime.date(2026, 8, 1), summary={})
    with pytest.raises(IntegrityError):
        DailyMISLog.objects.create(hospital=hospital, report_date=datetime.date(2026, 8, 1), summary={})


# --- Report APIViews -----------------------------------------------------
#
# All seven are plain GET/IsAuthenticated views, scoped by `request.user.hospital`
# inside the service call (never routed through TenantScopedViewSetMixin, so
# unaffected by the list/retrieve/update/destroy bug) and default to the last
# 7 days when `?start=/?end=` aren't given.


@pytest.mark.django_db
def test_report_endpoints_reject_unauthenticated_requests(api_client):
    response = api_client.get("/api/v1/reports/call-performance/")
    assert response.status_code == 401


@pytest.mark.django_db
def test_call_performance_report_reflects_seeded_calls_in_default_window(auth_client, hospital):
    now = timezone.now()
    Call.objects.create(hospital=hospital, direction=Call.Direction.INBOUND, status=Call.Status.ANSWERED, from_number="9000000001", started_at=now, duration_seconds=120)
    Call.objects.create(hospital=hospital, direction=Call.Direction.INBOUND, status=Call.Status.MISSED, from_number="9000000002", started_at=now)
    Call.objects.create(hospital=hospital, direction=Call.Direction.INBOUND, status=Call.Status.BUSY, from_number="9000000003", started_at=now)

    response = auth_client.get("/api/v1/reports/call-performance/")

    assert response.status_code == 200
    assert response.data["received"] == 3
    assert response.data["answered"] == 1
    # BUSY isn't MISSED/RNR, so only the one MISSED call counts.
    assert response.data["missed"] == 1
    assert response.data["avg_duration_seconds"] == pytest.approx(120.0)


@pytest.mark.django_db
def test_call_performance_report_excludes_calls_outside_the_default_seven_day_window(auth_client, hospital):
    stale = Call.objects.create(hospital=hospital, direction=Call.Direction.INBOUND, status=Call.Status.ANSWERED, from_number="9000000004", started_at=timezone.now())
    Call.objects.filter(pk=stale.pk).update(started_at=timezone.now() - datetime.timedelta(days=10))

    response = auth_client.get("/api/v1/reports/call-performance/")

    assert response.status_code == 200
    assert response.data["received"] == 0
    assert response.data["avg_duration_seconds"] is None


@pytest.mark.django_db
def test_call_performance_report_honours_explicit_start_end_window(auth_client, hospital):
    started_at = timezone.make_aware(datetime.datetime.combine(datetime.date(2026, 8, 1), datetime.time(9, 0)))
    Call.objects.create(hospital=hospital, direction=Call.Direction.INBOUND, status=Call.Status.ANSWERED, from_number="9000000005", started_at=started_at)
    # Outside the requested window entirely.
    Call.objects.create(hospital=hospital, direction=Call.Direction.INBOUND, status=Call.Status.ANSWERED, from_number="9000000006", started_at=timezone.now())

    response = auth_client.get("/api/v1/reports/call-performance/", {"start": "2026-08-01", "end": "2026-08-02"})

    assert response.status_code == 200
    assert response.data["received"] == 1


@pytest.mark.django_db
def test_call_performance_report_does_not_include_another_hospitals_calls(auth_client, hospital, other_hospital):
    now = timezone.now()
    Call.objects.create(hospital=hospital, direction=Call.Direction.INBOUND, status=Call.Status.ANSWERED, from_number="9000000007", started_at=now, duration_seconds=10)
    Call.objects.create(hospital=other_hospital, direction=Call.Direction.INBOUND, status=Call.Status.ANSWERED, from_number="9000000008", started_at=now, duration_seconds=9999)

    response = auth_client.get("/api/v1/reports/call-performance/")

    assert response.status_code == 200
    assert response.data["received"] == 1
    assert response.data["avg_duration_seconds"] == pytest.approx(10.0)


@pytest.mark.django_db
def test_enquiry_funnel_report_reflects_seeded_enquiries(auth_client, hospital):
    Enquiry.objects.create(hospital=hospital, name="A", mobile="9000000010", source=Enquiry.Source.WALK_IN)
    Enquiry.objects.create(hospital=hospital, name="B", mobile="9000000011", source=Enquiry.Source.WALK_IN)
    Enquiry.objects.create(hospital=hospital, name="C", mobile="9000000012", source=Enquiry.Source.WALK_IN, stage=Enquiry.Stage.CONTACTED)

    response = auth_client.get("/api/v1/reports/enquiry-funnel/")

    assert response.status_code == 200
    assert response.data == {"new": 2, "contacted": 1}


@pytest.mark.django_db
def test_enquiry_funnel_report_excludes_enquiries_outside_the_window(auth_client, hospital):
    stale = Enquiry.objects.create(hospital=hospital, name="Old", mobile="9000000013", source=Enquiry.Source.WALK_IN)
    Enquiry.objects.filter(pk=stale.pk).update(created_at=timezone.now() - datetime.timedelta(days=30))

    response = auth_client.get("/api/v1/reports/enquiry-funnel/")

    assert response.status_code == 200
    assert response.data == {}


@pytest.mark.django_db
def test_enquiry_funnel_report_does_not_include_another_hospitals_enquiries(auth_client, hospital, other_hospital):
    Enquiry.objects.create(hospital=hospital, name="Mine", mobile="9000000014", source=Enquiry.Source.WALK_IN)
    Enquiry.objects.create(hospital=other_hospital, name="NotMine", mobile="9000000015", source=Enquiry.Source.WALK_IN)

    response = auth_client.get("/api/v1/reports/enquiry-funnel/")

    assert response.status_code == 200
    assert response.data == {"new": 1}


@pytest.mark.django_db
def test_department_doctor_volume_report_smoke_with_no_data(auth_client):
    response = auth_client.get("/api/v1/reports/department-doctor-volume/")
    assert response.status_code == 200
    assert response.data == {"rows": []}


@pytest.mark.django_db
def test_department_doctor_volume_report_reflects_seeded_appointment(auth_client, hospital, doctor, patient, slot):
    book_appointment(patient=patient, slot=slot)

    response = auth_client.get("/api/v1/reports/department-doctor-volume/")

    assert response.status_code == 200
    rows = response.data["rows"]
    assert len(rows) == 1
    assert rows[0]["doctor__name"] == doctor.name
    assert rows[0]["booked"] == 1
    assert rows[0]["completed"] == 0
    assert rows[0]["no_show"] == 0


@pytest.mark.django_db
def test_no_show_effectiveness_report_smoke_with_no_data(auth_client):
    response = auth_client.get("/api/v1/reports/no-show-effectiveness/")
    assert response.status_code == 200
    assert response.data == {"no_shows": 0, "recall_tasks_done": 0}


@pytest.mark.django_db
def test_no_show_effectiveness_report_reflects_seeded_no_show_and_recall_task(auth_client, hospital, doctor, patient, slot):
    appointment = book_appointment(patient=patient, slot=slot)
    mark_no_show(appointment)
    Task.objects.create(
        hospital=hospital, title="Recall no-show",
        content_type=ContentType.objects.get_for_model(Appointment), object_id=appointment.id,
        status=Task.Status.DONE,
    )

    response = auth_client.get("/api/v1/reports/no-show-effectiveness/")

    assert response.status_code == 200
    assert response.data["no_shows"] == 1
    assert response.data["recall_tasks_done"] == 1


@pytest.mark.django_db
def test_daily_mis_preview_report_smoke(auth_client, hospital):
    response = auth_client.get("/api/v1/reports/daily-mis-preview/")

    assert response.status_code == 200
    assert "summary" in response.data
    assert "text" in response.data
    assert response.data["summary"]["date"] == timezone.localdate().isoformat()
    assert hospital.name in response.data["text"]


@pytest.mark.django_db
def test_revenue_by_source_report_smoke_with_no_data(auth_client):
    response = auth_client.get("/api/v1/reports/revenue-by-source/")
    assert response.status_code == 200
    assert response.data["rows"] == []
    assert "note" in response.data


@pytest.mark.django_db
def test_revenue_by_source_report_attributes_billing_to_enquiry_source(auth_client, hospital, patient):
    Enquiry.objects.create(hospital=hospital, name="Asha", mobile=patient.mobile, source=Enquiry.Source.WALK_IN, patient=patient)
    HISBillingRecord.objects.create(
        hospital=hospital, patient=patient, external_bill_id="BILL-1",
        bill_date=timezone.localdate(), total_amount=Decimal("1500.00"), status=HISBillingRecord.Status.PAID,
    )

    response = auth_client.get("/api/v1/reports/revenue-by-source/")

    assert response.status_code == 200
    row = next(r for r in response.data["rows"] if r["source"] == "walk_in")
    assert row["enquiry_count"] == 1
    assert row["billed_amount"] == Decimal("1500.00")


@pytest.mark.django_db
def test_reminder_delivery_summary_report_smoke_with_no_data(auth_client):
    response = auth_client.get("/api/v1/reports/reminder-delivery/")
    assert response.status_code == 200
    assert response.data == {"rows": []}


@pytest.mark.django_db
def test_doctor_revenue_report_smoke_with_no_data(auth_client):
    response = auth_client.get("/api/v1/reports/doctor-revenue/")
    assert response.status_code == 200
    assert response.data["rows"] == []
    assert "note" in response.data


@pytest.mark.django_db
def test_doctor_revenue_report_attributes_billing_to_the_completing_doctor(auth_client, hospital, patient, department):
    doctor = Doctor.objects.create(hospital=hospital, department=department, name="Rao")
    slot = Slot.objects.create(
        hospital=hospital, doctor=doctor, date=timezone.localdate(),
        start_time=datetime.time(10, 0), end_time=datetime.time(10, 15),
    )
    appointment = book_appointment(patient=patient, slot=slot)
    appointment.status = Appointment.Status.COMPLETED
    appointment.completed_at = timezone.now()
    appointment.save(update_fields=["status", "completed_at"])

    HISBillingRecord.objects.create(
        hospital=hospital, patient=patient, external_bill_id="BILL-DR-1",
        bill_date=timezone.localdate(), total_amount=Decimal("3000.00"), status=HISBillingRecord.Status.PAID,
    )

    response = auth_client.get("/api/v1/reports/doctor-revenue/")

    assert response.status_code == 200
    row = next(r for r in response.data["rows"] if r["doctor_id"] == doctor.id)
    assert row["completed_appointments"] == 1
    assert row["billed_amount"] == Decimal("3000.00")


@pytest.mark.django_db
def test_opd_snapshot_reflects_todays_check_ins_and_completions(auth_client, hospital, doctor, patient, slot):
    from apps.appointments.services import check_in, complete, start_consult

    appointment = book_appointment(patient=patient, slot=slot)
    check_in(appointment)

    response = auth_client.get("/api/v1/reports/opd-snapshot/")
    assert response.status_code == 200
    assert response.data["encounters_today"] == 1
    assert response.data["waiting"] == 1
    assert response.data["in_consult"] == 0
    assert response.data["completed_today"] == 0

    start_consult(appointment)
    complete(appointment)

    response = auth_client.get("/api/v1/reports/opd-snapshot/")
    assert response.data["waiting"] == 0
    assert response.data["completed_today"] == 1


@pytest.mark.django_db
def test_opd_snapshot_smoke_with_no_data(auth_client):
    response = auth_client.get("/api/v1/reports/opd-snapshot/")
    assert response.status_code == 200
    assert response.data == {"encounters_today": 0, "waiting": 0, "in_consult": 0, "completed_today": 0}


@pytest.mark.django_db
def test_bed_occupancy_reflects_admissions(auth_client, hospital, doctor, patient):
    from apps.facilities.models import Bed, Room, Ward
    from apps.ipd.services import admit_patient

    ward = Ward.objects.create(hospital=hospital, name="General Ward A")
    room = Room.objects.create(hospital=hospital, ward=ward, room_number="101")
    bed1 = Bed.objects.create(hospital=hospital, room=room, bed_number="A")
    Bed.objects.create(hospital=hospital, room=room, bed_number="B")

    response = auth_client.get("/api/v1/reports/bed-occupancy/")
    assert response.status_code == 200
    assert response.data == {"total_beds": 2, "occupied_beds": 0, "occupancy_pct": 0}

    admit_patient(hospital=hospital, patient=patient, admitting_doctor=doctor, bed=bed1)

    response = auth_client.get("/api/v1/reports/bed-occupancy/")
    assert response.data == {"total_beds": 2, "occupied_beds": 1, "occupancy_pct": 50.0}


@pytest.mark.django_db
def test_bed_occupancy_smoke_with_no_beds(auth_client):
    response = auth_client.get("/api/v1/reports/bed-occupancy/")
    assert response.status_code == 200
    assert response.data == {"total_beds": 0, "occupied_beds": 0, "occupancy_pct": 0}


@pytest.mark.django_db
def test_lab_tat_reflects_verified_results_and_pending_orders(auth_client, hospital, patient):
    from apps.laboratory.models import LabOrder, LabResult, LabTest

    lab_test = LabTest.objects.create(hospital=hospital, name="CBC")
    order = LabOrder.objects.create(hospital=hospital, patient=patient)
    order.ordered_tests.add(lab_test)

    response = auth_client.get("/api/v1/reports/lab-tat/")
    assert response.status_code == 200
    assert response.data == {"orders_today": 1, "pending_orders": 1, "avg_tat_minutes": None}

    result = LabResult.objects.create(hospital=hospital, lab_order=order, lab_test=lab_test, value="5.0")
    result.finalize(None)
    order.status = LabOrder.Status.VERIFIED
    order.save(update_fields=["status"])

    response = auth_client.get("/api/v1/reports/lab-tat/")
    assert response.data["orders_today"] == 1
    assert response.data["pending_orders"] == 0
    assert response.data["avg_tat_minutes"] is not None
    assert response.data["avg_tat_minutes"] >= 0


@pytest.mark.django_db
def test_lab_tat_smoke_with_no_data(auth_client):
    response = auth_client.get("/api/v1/reports/lab-tat/")
    assert response.status_code == 200
    assert response.data == {"orders_today": 0, "pending_orders": 0, "avg_tat_minutes": None}


@pytest.mark.django_db
def test_pharmacy_low_stock_flags_medicines_at_or_below_reorder_level(auth_client, hospital):
    import datetime

    from apps.pharmacy.models import Medicine, MedicineBatch

    low = Medicine.objects.create(hospital=hospital, name="Paracetamol", reorder_level=10)
    MedicineBatch.objects.create(hospital=hospital, medicine=low, batch_number="B1", expiry_date=datetime.date.today() + datetime.timedelta(days=100), quantity_available=5)

    healthy = Medicine.objects.create(hospital=hospital, name="Ibuprofen", reorder_level=10)
    MedicineBatch.objects.create(hospital=hospital, medicine=healthy, batch_number="B2", expiry_date=datetime.date.today() + datetime.timedelta(days=100), quantity_available=50)

    untracked = Medicine.objects.create(hospital=hospital, name="Vitamin C", reorder_level=0)
    MedicineBatch.objects.create(hospital=hospital, medicine=untracked, batch_number="B3", expiry_date=datetime.date.today() + datetime.timedelta(days=100), quantity_available=0)

    response = auth_client.get("/api/v1/reports/pharmacy-low-stock/")
    assert response.status_code == 200
    assert response.data["total_medicines"] == 3
    assert response.data["low_stock_count"] == 1
    assert response.data["low_stock_medicines"][0]["name"] == "Paracetamol"
    assert response.data["low_stock_medicines"][0]["available"] == 5


@pytest.mark.django_db
def test_pharmacy_low_stock_smoke_with_no_data(auth_client):
    response = auth_client.get("/api/v1/reports/pharmacy-low-stock/")
    assert response.status_code == 200
    assert response.data == {"total_medicines": 0, "low_stock_count": 0, "low_stock_medicines": []}
