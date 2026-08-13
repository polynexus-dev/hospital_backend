from contextlib import suppress
from decimal import Decimal

import pytest

from apps.patients.models import Patient
from apps.tpa.models import PreAuthRequest, TPACompany


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
def tpa_company(hospital):
    created = TPACompany.objects.create(hospital=hospital, name="Star Health", code="STAR01")
    yield created
    _teardown(created)


@pytest.fixture
def other_tpa_company(other_hospital):
    created = TPACompany.objects.create(hospital=other_hospital, name="Other Insurer", code="OTH01")
    yield created
    _teardown(created)


# --- TPACompany: model-level CRUD ------------------------------------------


@pytest.mark.django_db
def test_tpa_company_model_create_with_required_fields_only(hospital):
    company = TPACompany.objects.create(hospital=hospital, name="Bajaj Allianz", code="BAJ01")
    assert company.pk is not None
    assert company.avg_tat_days == 2
    assert company.is_active is True
    _teardown(company)


@pytest.mark.django_db
def test_tpa_company_model_create_with_full_fields(hospital):
    company = TPACompany.objects.create(
        hospital=hospital, name="ICICI Lombard", code="ICICI01", contact_person="Ramesh Iyer", phone="9822000001",
        email="claims@icicilombard.example", claim_submission_email="submit@icicilombard.example",
        avg_tat_days=5, is_active=False,
    )
    assert company.contact_person == "Ramesh Iyer"
    assert company.avg_tat_days == 5
    assert company.is_active is False
    _teardown(company)


@pytest.mark.django_db
def test_tpa_company_model_retrieve(tpa_company):
    fetched = TPACompany.objects.get(pk=tpa_company.pk)
    assert fetched.name == "Star Health"


@pytest.mark.django_db
def test_tpa_company_model_update(tpa_company):
    tpa_company.avg_tat_days = 7
    tpa_company.is_active = False
    tpa_company.save()
    tpa_company.refresh_from_db()
    assert tpa_company.avg_tat_days == 7
    assert tpa_company.is_active is False


@pytest.mark.django_db
def test_tpa_company_model_delete(hospital):
    company = TPACompany.objects.create(hospital=hospital, name="Delete Me", code="DEL01")
    company_id = company.pk
    company.delete()
    assert not TPACompany.objects.filter(pk=company_id).exists()


# --- PreAuthRequest: model-level CRUD --------------------------------------


@pytest.mark.django_db
def test_pre_auth_request_model_create_with_required_fields_only(tpa_company, patient):
    request = PreAuthRequest.objects.create(
        hospital=tpa_company.hospital, patient=patient, tpa_company=tpa_company,
        policy_number="POL123456", claim_amount=Decimal("100000.00"),
    )
    assert request.pk is not None
    assert request.status == PreAuthRequest.Status.SUBMITTED
    assert request.approved_amount == Decimal("0.00")
    assert request.checklist == {}
    _teardown(request)


@pytest.mark.django_db
def test_pre_auth_request_model_create_with_full_fields(tpa_company, patient):
    request = PreAuthRequest.objects.create(
        hospital=tpa_company.hospital, patient=patient, tpa_company=tpa_company,
        policy_number="POL999999", claim_amount=Decimal("250000.00"), approved_amount=Decimal("200000.00"),
        status=PreAuthRequest.Status.APPROVED, checklist={"kyc": True, "policy_copy": True},
    )
    assert request.approved_amount == Decimal("200000.00")
    assert request.status == PreAuthRequest.Status.APPROVED
    assert request.checklist == {"kyc": True, "policy_copy": True}
    _teardown(request)


@pytest.mark.django_db
def test_pre_auth_request_model_retrieve(tpa_company, patient):
    request = PreAuthRequest.objects.create(hospital=tpa_company.hospital, patient=patient, tpa_company=tpa_company, policy_number="POL1", claim_amount=Decimal("1000.00"))
    fetched = PreAuthRequest.objects.get(pk=request.pk)
    assert fetched.policy_number == "POL1"
    _teardown(request)


@pytest.mark.django_db
def test_pre_auth_request_model_update(tpa_company, patient):
    request = PreAuthRequest.objects.create(hospital=tpa_company.hospital, patient=patient, tpa_company=tpa_company, policy_number="POL1", claim_amount=Decimal("1000.00"))
    request.status = PreAuthRequest.Status.QUERY_RAISED
    request.checklist = {"kyc": False}
    request.save()
    request.refresh_from_db()
    assert request.status == PreAuthRequest.Status.QUERY_RAISED
    assert request.checklist == {"kyc": False}
    _teardown(request)


@pytest.mark.django_db
def test_pre_auth_request_model_delete(tpa_company, patient):
    request = PreAuthRequest.objects.create(hospital=tpa_company.hospital, patient=patient, tpa_company=tpa_company, policy_number="POL1", claim_amount=Decimal("1000.00"))
    request_id = request.pk
    request.delete()
    assert not PreAuthRequest.objects.filter(pk=request_id).exists()


# --- TPACompanyViewSet: API-level CRUD -------------------------------------


@pytest.mark.django_db
def test_tpa_company_api_create_with_required_fields_only(auth_client, hospital):
    response = auth_client.post("/api/v1/tpa/companies/", {"name": "New Insurer", "code": "NEW01"}, format="json")
    assert response.status_code == 201
    created = TPACompany.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id
    assert created.name == "New Insurer"


@pytest.mark.django_db
def test_tpa_company_api_list(auth_client, tpa_company):
    response = auth_client.get("/api/v1/tpa/companies/")
    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert tpa_company.id in ids


@pytest.mark.django_db
def test_tpa_company_api_retrieve(auth_client, tpa_company):
    response = auth_client.get(f"/api/v1/tpa/companies/{tpa_company.id}/")
    assert response.status_code == 200
    assert response.data["name"] == "Star Health"


@pytest.mark.django_db
def test_tpa_company_api_update(auth_client, tpa_company):
    response = auth_client.patch(f"/api/v1/tpa/companies/{tpa_company.id}/", {"avg_tat_days": 10}, format="json")
    assert response.status_code == 200
    tpa_company.refresh_from_db()
    assert tpa_company.avg_tat_days == 10


@pytest.mark.django_db
def test_tpa_company_api_delete(auth_client, tpa_company):
    response = auth_client.delete(f"/api/v1/tpa/companies/{tpa_company.id}/")
    assert response.status_code == 204
    assert not TPACompany.objects.filter(pk=tpa_company.id).exists()


@pytest.mark.django_db
def test_tpa_company_api_filters_by_is_active(auth_client, hospital):
    active = TPACompany.objects.create(hospital=hospital, name="Active Co", code="ACT01", is_active=True)
    inactive = TPACompany.objects.create(hospital=hospital, name="Inactive Co", code="INA01", is_active=False)

    response = auth_client.get("/api/v1/tpa/companies/?is_active=true")

    ids = {row["id"] for row in response.data["results"]}
    assert active.id in ids
    assert inactive.id not in ids


@pytest.mark.django_db
def test_tpa_company_api_search_by_name(auth_client, hospital):
    match = TPACompany.objects.create(hospital=hospital, name="Findable Insurer", code="FND01")
    other = TPACompany.objects.create(hospital=hospital, name="Different Co", code="DIF01")

    response = auth_client.get("/api/v1/tpa/companies/?search=Findable")

    ids = {row["id"] for row in response.data["results"]}
    assert match.id in ids
    assert other.id not in ids


# --- PreAuthRequestViewSet: API-level CRUD ---------------------------------


@pytest.mark.django_db
def test_pre_auth_request_api_create_with_required_fields_only(auth_client, hospital, tpa_company, patient):
    response = auth_client.post(
        "/api/v1/tpa/pre-auth/",
        {"patient": patient.id, "tpa_company": tpa_company.id, "policy_number": "POL777", "claim_amount": "50000.00"},
        format="json",
    )
    assert response.status_code == 201
    created = PreAuthRequest.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id
    assert response.data["patient_name"] == patient.full_name
    assert response.data["tpa_name"] == tpa_company.name


@pytest.mark.django_db
def test_pre_auth_request_api_list(auth_client, tpa_company, patient):
    request = PreAuthRequest.objects.create(hospital=tpa_company.hospital, patient=patient, tpa_company=tpa_company, policy_number="POL1", claim_amount=Decimal("1000.00"))
    response = auth_client.get("/api/v1/tpa/pre-auth/")
    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert request.id in ids


@pytest.mark.django_db
def test_pre_auth_request_api_retrieve(auth_client, tpa_company, patient):
    request = PreAuthRequest.objects.create(hospital=tpa_company.hospital, patient=patient, tpa_company=tpa_company, policy_number="POL1", claim_amount=Decimal("1000.00"))
    response = auth_client.get(f"/api/v1/tpa/pre-auth/{request.id}/")
    assert response.status_code == 200
    assert response.data["policy_number"] == "POL1"


@pytest.mark.django_db
def test_pre_auth_request_api_update(auth_client, tpa_company, patient):
    request = PreAuthRequest.objects.create(hospital=tpa_company.hospital, patient=patient, tpa_company=tpa_company, policy_number="POL1", claim_amount=Decimal("1000.00"))
    response = auth_client.patch(
        f"/api/v1/tpa/pre-auth/{request.id}/", {"status": PreAuthRequest.Status.APPROVED, "approved_amount": "900.00"}, format="json",
    )
    assert response.status_code == 200
    request.refresh_from_db()
    assert request.status == PreAuthRequest.Status.APPROVED
    assert request.approved_amount == Decimal("900.00")


@pytest.mark.django_db
def test_pre_auth_request_api_delete(auth_client, tpa_company, patient):
    request = PreAuthRequest.objects.create(hospital=tpa_company.hospital, patient=patient, tpa_company=tpa_company, policy_number="POL1", claim_amount=Decimal("1000.00"))
    response = auth_client.delete(f"/api/v1/tpa/pre-auth/{request.id}/")
    assert response.status_code == 204
    assert not PreAuthRequest.objects.filter(pk=request.id).exists()


@pytest.mark.django_db
def test_pre_auth_request_api_filters_by_status(auth_client, tpa_company, patient):
    submitted = PreAuthRequest.objects.create(hospital=tpa_company.hospital, patient=patient, tpa_company=tpa_company, policy_number="P1", claim_amount=Decimal("1"), status=PreAuthRequest.Status.SUBMITTED)
    approved = PreAuthRequest.objects.create(hospital=tpa_company.hospital, patient=patient, tpa_company=tpa_company, policy_number="P2", claim_amount=Decimal("1"), status=PreAuthRequest.Status.APPROVED)

    response = auth_client.get("/api/v1/tpa/pre-auth/?status=approved")

    ids = {row["id"] for row in response.data["results"]}
    assert approved.id in ids
    assert submitted.id not in ids


# --- Cross-tenant isolation -------------------------------------------------


@pytest.mark.django_db
def test_tpa_company_list_only_returns_the_authenticated_users_hospital_data(auth_client, tpa_company, other_tpa_company):
    response = auth_client.get("/api/v1/tpa/companies/")
    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert ids == {tpa_company.id}


@pytest.mark.django_db
def test_tpa_company_retrieve_404s_for_another_hospitals_object(auth_client, other_tpa_company):
    response = auth_client.get(f"/api/v1/tpa/companies/{other_tpa_company.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_tpa_company_update_cannot_modify_another_hospitals_object(auth_client, other_tpa_company):
    response = auth_client.patch(f"/api/v1/tpa/companies/{other_tpa_company.id}/", {"name": "Hijacked"}, format="json")
    assert response.status_code == 404
    other_tpa_company.refresh_from_db()
    assert other_tpa_company.name == "Other Insurer"


@pytest.mark.django_db
def test_tpa_company_destroy_cannot_delete_another_hospitals_object(auth_client, other_tpa_company):
    response = auth_client.delete(f"/api/v1/tpa/companies/{other_tpa_company.id}/")
    assert response.status_code == 404
    assert TPACompany.objects.filter(pk=other_tpa_company.id).exists()


@pytest.mark.django_db
def test_pre_auth_request_list_only_returns_the_authenticated_users_hospital_data(auth_client, tpa_company, patient, other_tpa_company, other_patient):
    mine = PreAuthRequest.objects.create(hospital=tpa_company.hospital, patient=patient, tpa_company=tpa_company, policy_number="MINE", claim_amount=Decimal("1"))
    PreAuthRequest.objects.create(hospital=other_tpa_company.hospital, patient=other_patient, tpa_company=other_tpa_company, policy_number="NOTMINE", claim_amount=Decimal("1"))

    response = auth_client.get("/api/v1/tpa/pre-auth/")

    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert ids == {mine.id}


@pytest.mark.django_db
def test_pre_auth_request_retrieve_404s_for_another_hospitals_object(auth_client, other_tpa_company, other_patient):
    theirs = PreAuthRequest.objects.create(hospital=other_tpa_company.hospital, patient=other_patient, tpa_company=other_tpa_company, policy_number="NOTMINE", claim_amount=Decimal("1"))
    response = auth_client.get(f"/api/v1/tpa/pre-auth/{theirs.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_pre_auth_request_update_cannot_modify_another_hospitals_object(auth_client, other_tpa_company, other_patient):
    theirs = PreAuthRequest.objects.create(hospital=other_tpa_company.hospital, patient=other_patient, tpa_company=other_tpa_company, policy_number="NOTMINE", claim_amount=Decimal("1"))
    response = auth_client.patch(f"/api/v1/tpa/pre-auth/{theirs.id}/", {"status": PreAuthRequest.Status.APPROVED}, format="json")
    assert response.status_code == 404
    theirs.refresh_from_db()
    assert theirs.status == PreAuthRequest.Status.SUBMITTED


@pytest.mark.django_db
def test_pre_auth_request_destroy_cannot_delete_another_hospitals_object(auth_client, other_tpa_company, other_patient):
    theirs = PreAuthRequest.objects.create(hospital=other_tpa_company.hospital, patient=other_patient, tpa_company=other_tpa_company, policy_number="NOTMINE", claim_amount=Decimal("1"))
    response = auth_client.delete(f"/api/v1/tpa/pre-auth/{theirs.id}/")
    assert response.status_code == 404
    assert PreAuthRequest.objects.filter(pk=theirs.id).exists()


@pytest.mark.django_db
def test_create_still_stamps_the_requesting_users_hospital(auth_client, hospital):
    response = auth_client.post(
        "/api/v1/tpa/companies/", {"name": "Spoof Attempt", "code": "SP01", "hospital": "should-be-ignored"}, format="json",
    )
    assert response.status_code == 201
    created = TPACompany.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id
