import datetime
from contextlib import suppress
from decimal import Decimal

import pytest

from apps.patients.models import Patient
from apps.referrals.models import FieldVisit, ReferralRecord, ReferringDoctor


def _teardown(instance):
    """Mirrors Backend/conftest.py's `_teardown` — explicit, best-effort
    delete on top of pytest-django's implicit per-test transaction
    rollback. See conftest.py for the full rationale."""
    with suppress(Exception):
        instance.delete()


@pytest.fixture
def patient(hospital):
    created = Patient.objects.create(hospital=hospital, first_name="Asha", mobile="9000000001")
    yield created
    _teardown(created)


@pytest.fixture
def other_patient(other_hospital):
    created = Patient.objects.create(hospital=other_hospital, first_name="Neha", mobile="9000000002")
    yield created
    _teardown(created)


@pytest.fixture
def referring_doctor(hospital):
    created = ReferringDoctor.objects.create(hospital=hospital, name="Dr. Kulkarni", mobile="9800000001")
    yield created
    _teardown(created)


@pytest.fixture
def other_referring_doctor(other_hospital):
    created = ReferringDoctor.objects.create(hospital=other_hospital, name="Dr. NotMine", mobile="9800000002")
    yield created
    _teardown(created)


# --- ReferringDoctor: model-level CRUD -------------------------------------


@pytest.mark.django_db
def test_referring_doctor_model_create_with_required_fields_only(hospital):
    doctor = ReferringDoctor.objects.create(hospital=hospital, name="Dr. Patil", mobile="9811100001")
    assert doctor.pk is not None
    assert doctor.tier == ReferringDoctor.Tier.SILVER
    assert doctor.city == "Pune"
    assert doctor.is_active is True
    _teardown(doctor)


@pytest.mark.django_db
def test_referring_doctor_model_create_with_full_fields(hospital):
    doctor = ReferringDoctor.objects.create(
        hospital=hospital, name="Dr. Joshi", speciality="Cardiology", clinic_name="Joshi Clinic",
        mobile="9811100002", email="joshi@example.com", city="Mumbai", tier=ReferringDoctor.Tier.GOLD,
        date_of_birth=datetime.date(1975, 5, 1), notes="Long-time referrer", is_active=False,
    )
    assert doctor.tier == ReferringDoctor.Tier.GOLD
    assert doctor.city == "Mumbai"
    assert doctor.is_active is False
    _teardown(doctor)


@pytest.mark.django_db
def test_referring_doctor_model_retrieve(referring_doctor):
    fetched = ReferringDoctor.objects.get(pk=referring_doctor.pk)
    assert fetched.name == "Dr. Kulkarni"


@pytest.mark.django_db
def test_referring_doctor_model_update(referring_doctor):
    referring_doctor.tier = ReferringDoctor.Tier.GOLD
    referring_doctor.is_active = False
    referring_doctor.save()
    referring_doctor.refresh_from_db()
    assert referring_doctor.tier == ReferringDoctor.Tier.GOLD
    assert referring_doctor.is_active is False


@pytest.mark.django_db
def test_referring_doctor_model_delete(hospital):
    doctor = ReferringDoctor.objects.create(hospital=hospital, name="Delete Me", mobile="9800000009")
    doctor_id = doctor.pk
    doctor.delete()
    assert not ReferringDoctor.objects.filter(pk=doctor_id).exists()


# --- ReferralRecord: model-level CRUD --------------------------------------


@pytest.mark.django_db
def test_referral_record_model_create_with_required_fields_only(referring_doctor, patient):
    record = ReferralRecord.objects.create(hospital=referring_doctor.hospital, referring_doctor=referring_doctor, patient=patient)
    assert record.pk is not None
    assert record.status == ReferralRecord.Status.PENDING
    assert record.commission_percentage == Decimal("10.00")
    _teardown(record)


@pytest.mark.django_db
def test_referral_record_model_create_with_full_fields(referring_doctor, patient, department):
    record = ReferralRecord.objects.create(
        hospital=referring_doctor.hospital, referring_doctor=referring_doctor, patient=patient, department=department,
        attributed_revenue=Decimal("15000.00"), commission_percentage=Decimal("12.50"), status=ReferralRecord.Status.CONVERTED,
    )
    assert record.attributed_revenue == Decimal("15000.00")
    assert record.status == ReferralRecord.Status.CONVERTED
    _teardown(record)


@pytest.mark.django_db
def test_referral_record_model_update(referring_doctor, patient):
    record = ReferralRecord.objects.create(hospital=referring_doctor.hospital, referring_doctor=referring_doctor, patient=patient)
    record.status = ReferralRecord.Status.PAID
    record.attributed_revenue = Decimal("9000.00")
    record.save()
    record.refresh_from_db()
    assert record.status == ReferralRecord.Status.PAID
    assert record.attributed_revenue == Decimal("9000.00")
    _teardown(record)


@pytest.mark.django_db
def test_referral_record_model_delete(referring_doctor, patient):
    record = ReferralRecord.objects.create(hospital=referring_doctor.hospital, referring_doctor=referring_doctor, patient=patient)
    record_id = record.pk
    record.delete()
    assert not ReferralRecord.objects.filter(pk=record_id).exists()


# --- FieldVisit: model-level CRUD -------------------------------------------


@pytest.mark.django_db
def test_field_visit_model_create_with_required_fields_only(referring_doctor):
    visit = FieldVisit.objects.create(
        hospital=referring_doctor.hospital, referring_doctor=referring_doctor,
        visit_date=datetime.date(2026, 1, 10), notes="First visit, doctor was receptive.",
    )
    assert visit.pk is not None
    assert visit.outcome == ""
    _teardown(visit)


@pytest.mark.django_db
def test_field_visit_model_create_with_full_fields(referring_doctor, user):
    visit = FieldVisit.objects.create(
        hospital=referring_doctor.hospital, referring_doctor=referring_doctor, visited_by=user,
        visit_date=datetime.date(2026, 1, 10), notes="Follow-up visit.", outcome="Committed to refer more cases",
    )
    assert visit.visited_by_id == user.id
    assert visit.outcome == "Committed to refer more cases"
    _teardown(visit)


@pytest.mark.django_db
def test_field_visit_model_update(referring_doctor):
    visit = FieldVisit.objects.create(hospital=referring_doctor.hospital, referring_doctor=referring_doctor, visit_date=datetime.date(2026, 1, 10), notes="Initial notes")
    visit.notes = "Updated notes"
    visit.outcome = "Positive"
    visit.save()
    visit.refresh_from_db()
    assert visit.notes == "Updated notes"
    assert visit.outcome == "Positive"
    _teardown(visit)


@pytest.mark.django_db
def test_field_visit_model_delete(referring_doctor):
    visit = FieldVisit.objects.create(hospital=referring_doctor.hospital, referring_doctor=referring_doctor, visit_date=datetime.date(2026, 1, 10), notes="Delete me")
    visit_id = visit.pk
    visit.delete()
    assert not FieldVisit.objects.filter(pk=visit_id).exists()


# --- ReferringDoctorViewSet: API-level CRUD --------------------------------


@pytest.mark.django_db
def test_referring_doctor_api_create_with_required_fields_only(auth_client, hospital):
    response = auth_client.post("/api/v1/referrals/doctors/", {"name": "Dr. New", "mobile": "9822233344"}, format="json")
    assert response.status_code == 201
    created = ReferringDoctor.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id
    assert created.name == "Dr. New"


@pytest.mark.django_db
def test_referring_doctor_api_list(auth_client, referring_doctor):
    response = auth_client.get("/api/v1/referrals/doctors/")
    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert referring_doctor.id in ids


@pytest.mark.django_db
def test_referring_doctor_api_retrieve(auth_client, referring_doctor):
    response = auth_client.get(f"/api/v1/referrals/doctors/{referring_doctor.id}/")
    assert response.status_code == 200
    assert response.data["name"] == "Dr. Kulkarni"


@pytest.mark.django_db
def test_referring_doctor_api_update(auth_client, referring_doctor):
    response = auth_client.patch(f"/api/v1/referrals/doctors/{referring_doctor.id}/", {"tier": ReferringDoctor.Tier.GOLD}, format="json")
    assert response.status_code == 200
    referring_doctor.refresh_from_db()
    assert referring_doctor.tier == ReferringDoctor.Tier.GOLD


@pytest.mark.django_db
def test_referring_doctor_api_delete(auth_client, referring_doctor):
    response = auth_client.delete(f"/api/v1/referrals/doctors/{referring_doctor.id}/")
    assert response.status_code == 204
    assert not ReferringDoctor.objects.filter(pk=referring_doctor.id).exists()


# --- ReferralRecordViewSet: API-level CRUD ---------------------------------


@pytest.mark.django_db
def test_referral_record_api_create_with_required_fields_only(auth_client, hospital, referring_doctor, patient):
    response = auth_client.post(
        "/api/v1/referrals/records/", {"referring_doctor": referring_doctor.id, "patient": patient.id}, format="json",
    )
    assert response.status_code == 201
    created = ReferralRecord.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id
    assert response.data["referring_doctor_name"] == referring_doctor.name
    assert response.data["patient_name"] == patient.full_name


@pytest.mark.django_db
def test_referral_record_api_list(auth_client, referring_doctor, patient):
    record = ReferralRecord.objects.create(hospital=referring_doctor.hospital, referring_doctor=referring_doctor, patient=patient)
    response = auth_client.get("/api/v1/referrals/records/")
    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert record.id in ids


@pytest.mark.django_db
def test_referral_record_api_retrieve(auth_client, referring_doctor, patient):
    record = ReferralRecord.objects.create(hospital=referring_doctor.hospital, referring_doctor=referring_doctor, patient=patient)
    response = auth_client.get(f"/api/v1/referrals/records/{record.id}/")
    assert response.status_code == 200
    assert response.data["id"] == record.id


@pytest.mark.django_db
def test_referral_record_api_update(auth_client, referring_doctor, patient):
    record = ReferralRecord.objects.create(hospital=referring_doctor.hospital, referring_doctor=referring_doctor, patient=patient)
    response = auth_client.patch(f"/api/v1/referrals/records/{record.id}/", {"status": ReferralRecord.Status.CONVERTED}, format="json")
    assert response.status_code == 200
    record.refresh_from_db()
    assert record.status == ReferralRecord.Status.CONVERTED


@pytest.mark.django_db
def test_referral_record_api_delete(auth_client, referring_doctor, patient):
    record = ReferralRecord.objects.create(hospital=referring_doctor.hospital, referring_doctor=referring_doctor, patient=patient)
    response = auth_client.delete(f"/api/v1/referrals/records/{record.id}/")
    assert response.status_code == 204
    assert not ReferralRecord.objects.filter(pk=record.id).exists()


# --- FieldVisitViewSet: API-level CRUD -------------------------------------


@pytest.mark.django_db
def test_field_visit_api_create_with_required_fields_only(auth_client, hospital, referring_doctor):
    response = auth_client.post(
        "/api/v1/referrals/field-visits/",
        {"referring_doctor": referring_doctor.id, "visit_date": "2026-01-15", "notes": "Discussed referral pipeline."},
        format="json",
    )
    assert response.status_code == 201
    created = FieldVisit.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id
    assert created.notes == "Discussed referral pipeline."


@pytest.mark.django_db
def test_field_visit_api_create_without_notes_returns_400(auth_client, referring_doctor):
    """`FieldVisit.notes` has no `blank=True` — unlike almost every other
    'notes' field in this codebase, it's required for POST."""
    response = auth_client.post(
        "/api/v1/referrals/field-visits/", {"referring_doctor": referring_doctor.id, "visit_date": "2026-01-15"}, format="json",
    )
    assert response.status_code == 400
    assert "notes" in response.data


@pytest.mark.django_db
def test_field_visit_api_create_stamps_visited_by_to_requesting_user_even_if_spoofed(auth_client, user, other_user, referring_doctor):
    response = auth_client.post(
        "/api/v1/referrals/field-visits/",
        {"referring_doctor": referring_doctor.id, "visit_date": "2026-01-15", "notes": "notes", "visited_by": other_user.id},
        format="json",
    )
    assert response.status_code == 201
    created = FieldVisit.objects.get(pk=response.data["id"])
    assert created.visited_by_id == user.id


@pytest.mark.django_db
def test_field_visit_api_list(auth_client, referring_doctor):
    visit = FieldVisit.objects.create(hospital=referring_doctor.hospital, referring_doctor=referring_doctor, visit_date=datetime.date(2026, 1, 10), notes="x")
    response = auth_client.get("/api/v1/referrals/field-visits/")
    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert visit.id in ids


@pytest.mark.django_db
def test_field_visit_api_retrieve(auth_client, referring_doctor):
    visit = FieldVisit.objects.create(hospital=referring_doctor.hospital, referring_doctor=referring_doctor, visit_date=datetime.date(2026, 1, 10), notes="x")
    response = auth_client.get(f"/api/v1/referrals/field-visits/{visit.id}/")
    assert response.status_code == 200
    assert response.data["id"] == visit.id


@pytest.mark.django_db
def test_field_visit_api_update(auth_client, referring_doctor):
    visit = FieldVisit.objects.create(hospital=referring_doctor.hospital, referring_doctor=referring_doctor, visit_date=datetime.date(2026, 1, 10), notes="x")
    response = auth_client.patch(f"/api/v1/referrals/field-visits/{visit.id}/", {"outcome": "Signed MoU"}, format="json")
    assert response.status_code == 200
    visit.refresh_from_db()
    assert visit.outcome == "Signed MoU"


@pytest.mark.django_db
def test_field_visit_api_delete(auth_client, referring_doctor):
    visit = FieldVisit.objects.create(hospital=referring_doctor.hospital, referring_doctor=referring_doctor, visit_date=datetime.date(2026, 1, 10), notes="x")
    response = auth_client.delete(f"/api/v1/referrals/field-visits/{visit.id}/")
    assert response.status_code == 204
    assert not FieldVisit.objects.filter(pk=visit.id).exists()


# --- ReferringDoctorViewSet.league_table custom action ---------------------


@pytest.mark.django_db
def test_league_table_ranks_doctors_by_attributed_revenue(auth_client, hospital, patient):
    top = ReferringDoctor.objects.create(hospital=hospital, name="Top Doctor", mobile="9800000010")
    low = ReferringDoctor.objects.create(hospital=hospital, name="Low Doctor", mobile="9800000011")
    ReferralRecord.objects.create(hospital=hospital, referring_doctor=top, patient=patient, attributed_revenue=Decimal("50000.00"))
    ReferralRecord.objects.create(hospital=hospital, referring_doctor=low, patient=patient, attributed_revenue=Decimal("500.00"))

    response = auth_client.get("/api/v1/referrals/doctors/league-table/")

    assert response.status_code == 200
    names = [row["name"] for row in response.data]
    assert names.index("Top Doctor") < names.index("Low Doctor")


@pytest.mark.django_db
def test_league_table_never_shows_another_hospitals_doctors(auth_client, other_referring_doctor, other_patient):
    ReferralRecord.objects.create(
        hospital=other_referring_doctor.hospital, referring_doctor=other_referring_doctor, patient=other_patient,
        attributed_revenue=Decimal("100000.00"),
    )

    response = auth_client.get("/api/v1/referrals/doctors/league-table/")

    assert response.status_code == 200
    assert all(row["name"] != "Dr. NotMine" for row in response.data)


# --- Cross-tenant isolation -------------------------------------------------
#
# ReferringDoctorViewSet.get_queryset() calls `super().get_queryset()` (the
# mixin's fixed, per-request-filtered queryset) then `.annotate(...)` on top
# of it, same pattern as packages.CampaignViewSet — these tests confirm the
# fix survives being wrapped by that annotate() call too.


@pytest.mark.django_db
def test_referring_doctor_list_only_returns_the_authenticated_users_hospital_data(auth_client, referring_doctor, other_referring_doctor):
    response = auth_client.get("/api/v1/referrals/doctors/")
    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert ids == {referring_doctor.id}


@pytest.mark.django_db
def test_referring_doctor_retrieve_404s_for_another_hospitals_object(auth_client, other_referring_doctor):
    response = auth_client.get(f"/api/v1/referrals/doctors/{other_referring_doctor.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_referring_doctor_update_cannot_modify_another_hospitals_object(auth_client, other_referring_doctor):
    response = auth_client.patch(f"/api/v1/referrals/doctors/{other_referring_doctor.id}/", {"name": "Hijacked"}, format="json")
    assert response.status_code == 404
    other_referring_doctor.refresh_from_db()
    assert other_referring_doctor.name == "Dr. NotMine"


@pytest.mark.django_db
def test_referring_doctor_destroy_cannot_delete_another_hospitals_object(auth_client, other_referring_doctor):
    response = auth_client.delete(f"/api/v1/referrals/doctors/{other_referring_doctor.id}/")
    assert response.status_code == 404
    assert ReferringDoctor.objects.filter(pk=other_referring_doctor.id).exists()


@pytest.mark.django_db
def test_referral_record_list_only_returns_the_authenticated_users_hospital_data(auth_client, referring_doctor, patient, other_referring_doctor, other_patient):
    mine = ReferralRecord.objects.create(hospital=referring_doctor.hospital, referring_doctor=referring_doctor, patient=patient)
    ReferralRecord.objects.create(hospital=other_referring_doctor.hospital, referring_doctor=other_referring_doctor, patient=other_patient)

    response = auth_client.get("/api/v1/referrals/records/")

    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert ids == {mine.id}


@pytest.mark.django_db
def test_referral_record_retrieve_404s_for_another_hospitals_object(auth_client, other_referring_doctor, other_patient):
    theirs = ReferralRecord.objects.create(hospital=other_referring_doctor.hospital, referring_doctor=other_referring_doctor, patient=other_patient)
    response = auth_client.get(f"/api/v1/referrals/records/{theirs.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_referral_record_update_cannot_modify_another_hospitals_object(auth_client, other_referring_doctor, other_patient):
    theirs = ReferralRecord.objects.create(hospital=other_referring_doctor.hospital, referring_doctor=other_referring_doctor, patient=other_patient)
    response = auth_client.patch(f"/api/v1/referrals/records/{theirs.id}/", {"status": ReferralRecord.Status.PAID}, format="json")
    assert response.status_code == 404
    theirs.refresh_from_db()
    assert theirs.status == ReferralRecord.Status.PENDING


@pytest.mark.django_db
def test_referral_record_destroy_cannot_delete_another_hospitals_object(auth_client, other_referring_doctor, other_patient):
    theirs = ReferralRecord.objects.create(hospital=other_referring_doctor.hospital, referring_doctor=other_referring_doctor, patient=other_patient)
    response = auth_client.delete(f"/api/v1/referrals/records/{theirs.id}/")
    assert response.status_code == 404
    assert ReferralRecord.objects.filter(pk=theirs.id).exists()


@pytest.mark.django_db
def test_field_visit_list_only_returns_the_authenticated_users_hospital_data(auth_client, referring_doctor, other_referring_doctor):
    mine = FieldVisit.objects.create(hospital=referring_doctor.hospital, referring_doctor=referring_doctor, visit_date=datetime.date(2026, 1, 10), notes="mine")
    FieldVisit.objects.create(hospital=other_referring_doctor.hospital, referring_doctor=other_referring_doctor, visit_date=datetime.date(2026, 1, 10), notes="not mine")

    response = auth_client.get("/api/v1/referrals/field-visits/")

    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert ids == {mine.id}


@pytest.mark.django_db
def test_field_visit_retrieve_404s_for_another_hospitals_object(auth_client, other_referring_doctor):
    theirs = FieldVisit.objects.create(hospital=other_referring_doctor.hospital, referring_doctor=other_referring_doctor, visit_date=datetime.date(2026, 1, 10), notes="not mine")
    response = auth_client.get(f"/api/v1/referrals/field-visits/{theirs.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_field_visit_update_cannot_modify_another_hospitals_object(auth_client, other_referring_doctor):
    theirs = FieldVisit.objects.create(hospital=other_referring_doctor.hospital, referring_doctor=other_referring_doctor, visit_date=datetime.date(2026, 1, 10), notes="not mine")
    response = auth_client.patch(f"/api/v1/referrals/field-visits/{theirs.id}/", {"outcome": "Hijacked"}, format="json")
    assert response.status_code == 404
    theirs.refresh_from_db()
    assert theirs.outcome == ""


@pytest.mark.django_db
def test_field_visit_destroy_cannot_delete_another_hospitals_object(auth_client, other_referring_doctor):
    theirs = FieldVisit.objects.create(hospital=other_referring_doctor.hospital, referring_doctor=other_referring_doctor, visit_date=datetime.date(2026, 1, 10), notes="not mine")
    response = auth_client.delete(f"/api/v1/referrals/field-visits/{theirs.id}/")
    assert response.status_code == 404
    assert FieldVisit.objects.filter(pk=theirs.id).exists()


@pytest.mark.django_db
def test_create_still_stamps_the_requesting_users_hospital(auth_client, hospital):
    response = auth_client.post(
        "/api/v1/referrals/doctors/", {"name": "Spoof Attempt", "mobile": "9800000099", "hospital": "should-be-ignored"}, format="json",
    )
    assert response.status_code == 201
    created = ReferringDoctor.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id
