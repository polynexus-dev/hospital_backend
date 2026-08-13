import datetime
from contextlib import suppress
from decimal import Decimal

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.appointments.models import Doctor, Slot
from apps.appointments.services import book_appointment
from apps.enquiries.models import Enquiry
from apps.integrations.models import HISBillingRecord, HISVisit
from apps.patients.models import Patient


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
    created = Patient.objects.create(hospital=hospital, first_name="Asha", last_name="Patil", mobile="9822000001")
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


# --- HISVisitViewSet / HISBillingRecordViewSet ----------------------------
#
# Both are among the six viewsets the parallel audit confirmed were ALREADY
# safely tenant-scoped (explicit `filter(hospital_id=...)` in get_queryset,
# not routed through the vulnerable TenantScopedViewSetMixin pattern). Both
# are entirely read-only via the API — there is no write endpoint anywhere
# for either model, so every row here is seeded directly via the ORM
# (they're meant to be populated by the HIS sync connector/Celery task).


@pytest.mark.django_db
def test_his_visit_list_returns_only_the_authenticated_users_hospital(auth_client, hospital, other_hospital, patient):
    mine = HISVisit.objects.create(
        hospital=hospital, patient=patient, external_visit_id="V-1",
        visit_type=HISVisit.VisitType.OPD, visit_date=timezone.now(),
    )
    other_patient = Patient.objects.create(hospital=other_hospital, first_name="Not", mobile="9000000099")
    HISVisit.objects.create(
        hospital=other_hospital, patient=other_patient, external_visit_id="V-2",
        visit_type=HISVisit.VisitType.OPD, visit_date=timezone.now(),
    )

    response = auth_client.get("/api/v1/his-visits/")

    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert ids == {mine.id}


@pytest.mark.django_db
def test_his_visit_retrieve_404s_for_another_hospitals_visit(auth_client, other_hospital):
    other_patient = Patient.objects.create(hospital=other_hospital, first_name="Not", mobile="9000000098")
    theirs = HISVisit.objects.create(
        hospital=other_hospital, patient=other_patient, external_visit_id="V-3",
        visit_type=HISVisit.VisitType.IPD, visit_date=timezone.now(),
    )

    response = auth_client.get(f"/api/v1/his-visits/{theirs.id}/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_his_visit_retrieve_returns_full_fields_for_own_hospital(auth_client, hospital, patient):
    mine = HISVisit.objects.create(
        hospital=hospital, patient=patient, external_visit_id="V-4",
        visit_type=HISVisit.VisitType.DIAGNOSTIC, visit_date=timezone.now(),
        department_name="Radiology", doctor_name="Dr. Mehta",
    )

    response = auth_client.get(f"/api/v1/his-visits/{mine.id}/")

    assert response.status_code == 200
    assert response.data["external_visit_id"] == "V-4"
    assert response.data["visit_type"] == "diagnostic"
    assert response.data["department_name"] == "Radiology"
    assert response.data["doctor_name"] == "Dr. Mehta"
    assert response.data["patient"] == patient.id


@pytest.mark.django_db
def test_his_visit_list_can_filter_by_visit_type(auth_client, hospital, patient):
    HISVisit.objects.create(hospital=hospital, patient=patient, external_visit_id="V-5", visit_type=HISVisit.VisitType.OPD, visit_date=timezone.now())
    ipd = HISVisit.objects.create(hospital=hospital, patient=patient, external_visit_id="V-6", visit_type=HISVisit.VisitType.IPD, visit_date=timezone.now())

    response = auth_client.get("/api/v1/his-visits/", {"visit_type": "ipd"})

    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert ids == {ipd.id}


@pytest.mark.django_db
def test_his_visit_unauthenticated_request_is_rejected_not_unscoped(api_client):
    response = api_client.get("/api/v1/his-visits/")
    assert response.status_code == 401


@pytest.mark.django_db
def test_his_visit_is_unique_per_hospital_and_external_visit_id(hospital, patient):
    HISVisit.objects.create(hospital=hospital, patient=patient, external_visit_id="DUP-1", visit_type=HISVisit.VisitType.OPD, visit_date=timezone.now())
    with pytest.raises(IntegrityError):
        HISVisit.objects.create(hospital=hospital, patient=patient, external_visit_id="DUP-1", visit_type=HISVisit.VisitType.IPD, visit_date=timezone.now())


@pytest.mark.django_db
def test_his_billing_record_list_returns_only_the_authenticated_users_hospital(auth_client, hospital, other_hospital, patient):
    mine = HISBillingRecord.objects.create(
        hospital=hospital, patient=patient, external_bill_id="B-1", bill_date=timezone.localdate(),
        total_amount=Decimal("1000.00"), status=HISBillingRecord.Status.OUTSTANDING,
    )
    other_patient = Patient.objects.create(hospital=other_hospital, first_name="Not", mobile="9000000097")
    HISBillingRecord.objects.create(
        hospital=other_hospital, patient=other_patient, external_bill_id="B-2", bill_date=timezone.localdate(),
        total_amount=Decimal("5000.00"), status=HISBillingRecord.Status.PAID,
    )

    response = auth_client.get("/api/v1/his-billing/")

    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert ids == {mine.id}


@pytest.mark.django_db
def test_his_billing_record_retrieve_404s_for_another_hospitals_bill(auth_client, other_hospital):
    other_patient = Patient.objects.create(hospital=other_hospital, first_name="Not", mobile="9000000096")
    theirs = HISBillingRecord.objects.create(
        hospital=other_hospital, patient=other_patient, external_bill_id="B-3", bill_date=timezone.localdate(),
        total_amount=Decimal("2000.00"), status=HISBillingRecord.Status.PAID,
    )

    response = auth_client.get(f"/api/v1/his-billing/{theirs.id}/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_his_billing_record_retrieve_includes_computed_outstanding_amount(auth_client, hospital, patient):
    bill = HISBillingRecord.objects.create(
        hospital=hospital, patient=patient, external_bill_id="B-4", bill_date=timezone.localdate(),
        total_amount=Decimal("1000.00"), paid_amount=Decimal("400.00"), status=HISBillingRecord.Status.PARTIAL,
    )

    response = auth_client.get(f"/api/v1/his-billing/{bill.id}/")

    assert response.status_code == 200
    assert Decimal(response.data["outstanding_amount"]) == Decimal("600.00")


@pytest.mark.django_db
def test_his_billing_record_list_can_filter_by_status(auth_client, hospital, patient):
    paid = HISBillingRecord.objects.create(hospital=hospital, patient=patient, external_bill_id="B-5", bill_date=timezone.localdate(), total_amount=Decimal("100.00"), status=HISBillingRecord.Status.PAID)
    HISBillingRecord.objects.create(hospital=hospital, patient=patient, external_bill_id="B-6", bill_date=timezone.localdate(), total_amount=Decimal("100.00"), status=HISBillingRecord.Status.OUTSTANDING)

    response = auth_client.get("/api/v1/his-billing/", {"status": "paid"})

    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert ids == {paid.id}


@pytest.mark.django_db
def test_his_billing_record_unauthenticated_request_is_rejected_not_unscoped(api_client):
    response = api_client.get("/api/v1/his-billing/")
    assert response.status_code == 401


@pytest.mark.django_db
def test_his_billing_record_is_unique_per_hospital_and_external_bill_id(hospital, patient):
    HISBillingRecord.objects.create(hospital=hospital, patient=patient, external_bill_id="DUP-B", bill_date=timezone.localdate(), total_amount=Decimal("1.00"), status=HISBillingRecord.Status.PAID)
    with pytest.raises(IntegrityError):
        HISBillingRecord.objects.create(hospital=hospital, patient=patient, external_bill_id="DUP-B", bill_date=timezone.localdate(), total_amount=Decimal("2.00"), status=HISBillingRecord.Status.PAID)


# --- FHIRExportView --------------------------------------------------------


@pytest.mark.django_db
def test_fhir_export_requires_authentication(api_client):
    response = api_client.get("/api/v1/export/fhir/patients/")
    assert response.status_code == 401


@pytest.mark.django_db
def test_fhir_export_patients_returns_a_bundle_with_the_seeded_patient(auth_client, patient):
    response = auth_client.get("/api/v1/export/fhir/patients/")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    body = response.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "collection"
    assert body["total"] == 1
    entry = body["entry"][0]
    assert entry["resource"]["resourceType"] == "Patient"
    assert entry["resource"]["id"] == str(patient.id)
    assert entry["resource"]["name"][0]["text"] == "Asha Patil"


@pytest.mark.django_db
def test_fhir_export_patients_excludes_another_hospitals_patients(auth_client, hospital, other_hospital, patient):
    Patient.objects.create(hospital=other_hospital, first_name="Not", last_name="Mine", mobile="9000000095")

    response = auth_client.get("/api/v1/export/fhir/patients/")

    body = response.json()
    assert body["total"] == 1
    assert body["entry"][0]["resource"]["id"] == str(patient.id)


@pytest.mark.django_db
def test_fhir_export_appointments_returns_an_appointment_resource(auth_client, doctor, patient, slot):
    appointment = book_appointment(patient=patient, slot=slot)

    response = auth_client.get("/api/v1/export/fhir/appointments/")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    resource = body["entry"][0]["resource"]
    assert resource["resourceType"] == "Appointment"
    assert resource["id"] == str(appointment.id)
    assert resource["status"] == "booked"


@pytest.mark.django_db
def test_fhir_export_appointments_excludes_another_hospitals_appointments(auth_client, hospital, other_hospital, other_department, doctor, patient, slot):
    other_doctor = Doctor.objects.create(hospital=other_hospital, department=other_department, name="NotMine")
    other_patient = Patient.objects.create(hospital=other_hospital, first_name="Not", mobile="9000000094")
    other_slot = Slot.objects.create(hospital=other_hospital, doctor=other_doctor, date=timezone.localdate(), start_time=datetime.time(11, 0), end_time=datetime.time(11, 15))
    book_appointment(patient=other_patient, slot=other_slot)
    mine = book_appointment(patient=patient, slot=slot)

    response = auth_client.get("/api/v1/export/fhir/appointments/")

    body = response.json()
    assert body["total"] == 1
    assert body["entry"][0]["resource"]["id"] == str(mine.id)


@pytest.mark.django_db
def test_fhir_export_all_includes_both_patients_and_appointments(auth_client, doctor, patient, slot):
    book_appointment(patient=patient, slot=slot)

    response = auth_client.get("/api/v1/export/fhir/all/")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    resource_types = {entry["resource"]["resourceType"] for entry in body["entry"]}
    assert resource_types == {"Patient", "Appointment"}


@pytest.mark.django_db
def test_fhir_export_unknown_resource_type_returns_an_empty_bundle_not_an_error(auth_client, patient):
    """FHIRExportView only special-cases resource_type in {"patients",
    "appointments", "all"} — anything else matches neither `if` branch and
    silently falls through to an empty bundle with a 200, rather than
    rejecting the request with a 400. Documenting this as the real (if
    surprising) current behavior, verified by reading the view rather than
    assumed."""
    response = auth_client.get("/api/v1/export/fhir/nonsense/")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["entry"] == []


# --- DataExportView --------------------------------------------------------


@pytest.mark.django_db
def test_data_export_requires_authentication(api_client):
    response = api_client.get("/api/v1/export/patients/")
    assert response.status_code == 401


@pytest.mark.django_db
def test_data_export_patients_returns_csv_with_the_seeded_patient(auth_client, patient):
    response = auth_client.get("/api/v1/export/patients/")

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    content = response.content.decode()
    assert "attachment" in response["Content-Disposition"]
    assert patient.mobile in content
    assert patient.first_name in content


@pytest.mark.django_db
def test_data_export_patients_excludes_another_hospitals_data(auth_client, hospital, other_hospital, patient):
    other_patient = Patient.objects.create(hospital=other_hospital, first_name="Secret", mobile="9000000093")

    response = auth_client.get("/api/v1/export/patients/")

    content = response.content.decode()
    assert other_patient.mobile not in content
    assert "Secret" not in content


@pytest.mark.django_db
def test_data_export_enquiries_returns_csv_with_the_seeded_enquiry(auth_client, hospital):
    Enquiry.objects.create(hospital=hospital, name="Kunal Rao", mobile="9000000020", source=Enquiry.Source.WALK_IN)

    response = auth_client.get("/api/v1/export/enquiries/")

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    content = response.content.decode()
    assert "Kunal Rao" in content


@pytest.mark.django_db
def test_data_export_enquiries_excludes_another_hospitals_data(auth_client, hospital, other_hospital):
    Enquiry.objects.create(hospital=other_hospital, name="Secret Lead", mobile="9000000021", source=Enquiry.Source.WALK_IN)

    response = auth_client.get("/api/v1/export/enquiries/")

    content = response.content.decode()
    assert "Secret Lead" not in content


@pytest.mark.django_db
def test_data_export_appointments_returns_csv_with_the_seeded_appointment(auth_client, doctor, patient, slot):
    appointment = book_appointment(patient=patient, slot=slot)

    response = auth_client.get("/api/v1/export/appointments/")

    assert response.status_code == 200
    content = response.content.decode()
    assert str(appointment.id) in content
    assert str(appointment.doctor_id) in content


@pytest.mark.django_db
def test_data_export_unknown_model_name_returns_400(auth_client):
    response = auth_client.get("/api/v1/export/doctors/")
    assert response.status_code == 400


# --- IntegrationHealthView --------------------------------------------------
#
# IsAdminUser-gated (is_staff), unlike every other view in this app.


@pytest.mark.django_db
def test_integration_health_returns_200_for_staff_user(api_client, staff_user):
    api_client.force_authenticate(user=staff_user)

    response = api_client.get("/api/v1/integration-health/")

    assert response.status_code == 200
    for key in ("his_connector", "his_visits", "his_billing", "celery_tasks", "data_retention_days", "is_on_premise"):
        assert key in response.data


@pytest.mark.django_db
def test_integration_health_is_forbidden_for_a_non_staff_user(auth_client):
    response = auth_client.get("/api/v1/integration-health/")
    assert response.status_code == 403


@pytest.mark.django_db
def test_integration_health_requires_authentication(api_client):
    response = api_client.get("/api/v1/integration-health/")
    assert response.status_code in (401, 403)


@pytest.mark.django_db
def test_integration_health_reflects_seeded_his_counts(api_client, staff_user, hospital, patient):
    HISVisit.objects.create(hospital=hospital, patient=patient, external_visit_id="V-100", visit_type=HISVisit.VisitType.OPD, visit_date=timezone.now())
    HISBillingRecord.objects.create(hospital=hospital, patient=patient, external_bill_id="B-100", bill_date=timezone.localdate(), total_amount=Decimal("100.00"), status=HISBillingRecord.Status.PAID)
    api_client.force_authenticate(user=staff_user)

    response = api_client.get("/api/v1/integration-health/")

    assert response.status_code == 200
    assert response.data["his_visits"]["count"] == 1
    assert response.data["his_billing"]["count"] == 1
