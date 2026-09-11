import datetime
from unittest.mock import patch

import pytest

from apps.patients.models import Patient
from apps.tpa.models import TPACompany, PreAuthRequest

from .models import AbhaLink, ConsentRequest, HealthRecordFetch, NHCXTransaction


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Asha", last_name="Patil", mobile="9822011111")


@pytest.fixture
def other_patient(other_hospital):
    return Patient.objects.create(hospital=other_hospital, first_name="Theirs", mobile="9000000099")


# --- Stub gateway: real behavior, no mocking — this is what a fresh
# install with no ABDM_GATEWAY/NHCX_GATEWAY configured actually does. ----

@pytest.mark.django_db
def test_abha_link_create_returns_503_when_gateway_is_not_configured(auth_client, patient):
    response = auth_client.post("/api/v1/abdm/abha-links/", {
        "patient": str(patient.id), "verification_method": "mobile_otp", "identifier": "9822011111",
    }, format="json")

    assert response.status_code == 503
    assert not AbhaLink.objects.filter(patient=patient).exists()  # nothing left half-created


@pytest.mark.django_db
def test_consent_request_create_returns_503_when_gateway_is_not_configured(auth_client, hospital, patient):
    link = AbhaLink.objects.create(
        hospital=hospital, patient=patient, status=AbhaLink.Status.LINKED,
        verification_method=AbhaLink.VerificationMethod.MOBILE_OTP, abha_address="asha@abdm",
    )
    response = auth_client.post("/api/v1/abdm/consent-requests/", {
        "patient": str(patient.id), "abha_link": link.id, "purpose": "CAREMGT",
        "hi_types": ["Prescription"], "date_range_from": "2026-01-01", "date_range_to": "2026-06-01",
    }, format="json")

    assert response.status_code == 503
    assert not ConsentRequest.objects.filter(patient=patient).exists()


@pytest.mark.django_db
def test_nhcx_transaction_create_returns_503_when_gateway_is_not_configured(auth_client):
    response = auth_client.post("/api/v1/abdm/nhcx-transactions/", {"transaction_type": "eligibility_check"}, format="json")
    assert response.status_code == 503


# --- Happy path with a gateway actually wired up (a fake one, standing in
# for whatever real ABDMGateway/NHCXGateway eventually gets implemented) --

@pytest.mark.django_db
def test_abha_link_full_flow_with_a_configured_gateway(auth_client, hospital, user, patient):
    with patch("apps.abdm.views.get_abdm_gateway") as get_gateway:
        get_gateway.return_value.initiate_abha_verification.return_value = {"txn_id": "TXN-123"}
        response = auth_client.post("/api/v1/abdm/abha-links/", {
            "patient": str(patient.id), "verification_method": "mobile_otp", "identifier": "9822011111",
        }, format="json")
        assert response.status_code == 201
        link = AbhaLink.objects.get(patient=patient)
        assert link.status == AbhaLink.Status.PENDING
        assert link.gateway_txn_id == "TXN-123"
        assert link.initiated_by_id == user.id
        assert not link.abha_number

        get_gateway.return_value.verify_abha_otp.return_value = {"abha_number": "12-3456-7890-1234", "abha_address": "asha@abdm"}
        verify = auth_client.post(f"/api/v1/abdm/abha-links/{link.id}/verify/", {"otp": "123456"}, format="json")
        assert verify.status_code == 200

    link.refresh_from_db()
    assert link.status == AbhaLink.Status.LINKED
    assert link.abha_number == "12-3456-7890-1234"  # decrypts back transparently (apps.core.fields.EncryptedCharField)
    assert link.abha_address == "asha@abdm"
    assert link.linked_at is not None


@pytest.mark.django_db
def test_consent_request_full_flow_then_fetch_records(auth_client, hospital, user, patient):
    link = AbhaLink.objects.create(
        hospital=hospital, patient=patient, status=AbhaLink.Status.LINKED,
        verification_method=AbhaLink.VerificationMethod.MOBILE_OTP, abha_address="asha@abdm",
    )

    with patch("apps.abdm.views.get_abdm_gateway") as get_gateway:
        get_gateway.return_value.create_consent_request.return_value = {"gateway_request_id": "REQ-1"}
        create = auth_client.post("/api/v1/abdm/consent-requests/", {
            "patient": str(patient.id), "abha_link": link.id, "purpose": "CAREMGT",
            "hi_types": ["Prescription"], "date_range_from": "2026-01-01", "date_range_to": "2026-06-01",
        }, format="json")
        assert create.status_code == 201
        consent = ConsentRequest.objects.get(patient=patient)
        assert consent.status == ConsentRequest.Status.REQUESTED
        assert consent.gateway_request_id == "REQ-1"

        # Not granted yet — fetching records must be refused.
        premature = auth_client.post(f"/api/v1/abdm/consent-requests/{consent.id}/fetch_records/")
        assert premature.status_code == 400

        get_gateway.return_value.fetch_consent_status.return_value = {
            "status": ConsentRequest.Status.GRANTED, "consent_artifact_id": "ART-1", "expires_at": None,
        }
        status_check = auth_client.post(f"/api/v1/abdm/consent-requests/{consent.id}/check_status/")
        assert status_check.status_code == 200
        consent.refresh_from_db()
        assert consent.status == ConsentRequest.Status.GRANTED
        assert consent.consent_artifact_id == "ART-1"
        assert consent.responded_at is not None

        get_gateway.return_value.fetch_health_records.return_value = {"resourceType": "Bundle", "entry": [{"x": 1}, {"x": 2}]}
        fetch = auth_client.post(f"/api/v1/abdm/consent-requests/{consent.id}/fetch_records/")
        assert fetch.status_code == 200
        assert fetch.data["entry"] == [{"x": 1}, {"x": 2}]

    log = HealthRecordFetch.objects.get(consent_request=consent)
    assert log.fetched_by_id == user.id
    assert log.record_count == 2


@pytest.mark.django_db
def test_nhcx_transaction_full_flow(auth_client, hospital, user, patient):
    tpa_company = TPACompany.objects.create(hospital=hospital, name="Star Health", code="STAR")
    preauth = PreAuthRequest.objects.create(hospital=hospital, patient=patient, tpa_company=tpa_company, policy_number="POL1", claim_amount="1000.00")

    with patch("apps.abdm.views.get_nhcx_gateway") as get_gateway:
        get_gateway.return_value.submit_transaction.return_value = {"nhcx_transaction_id": "NHCX-1", "status": NHCXTransaction.Status.PENDING}
        create = auth_client.post("/api/v1/abdm/nhcx-transactions/", {
            "transaction_type": "pre_auth", "preauth_request": preauth.id,
        }, format="json")
        assert create.status_code == 201
        txn = NHCXTransaction.objects.get(preauth_request=preauth)
        assert txn.nhcx_transaction_id == "NHCX-1"
        assert txn.status == NHCXTransaction.Status.PENDING
        assert txn.initiated_by_id == user.id

        get_gateway.return_value.fetch_transaction_status.return_value = {"status": NHCXTransaction.Status.APPROVED, "summary": "Approved by payer"}
        check = auth_client.post(f"/api/v1/abdm/nhcx-transactions/{txn.id}/check_status/")
        assert check.status_code == 200

    txn.refresh_from_db()
    assert txn.status == NHCXTransaction.Status.APPROVED
    assert txn.gateway_response_summary == "Approved by payer"


# --- Tenant isolation --------------------------------------------------------

@pytest.mark.django_db
def test_abha_link_isolation(auth_client, other_hospital, other_patient):
    theirs = AbhaLink.objects.create(
        hospital=other_hospital, patient=other_patient, status=AbhaLink.Status.PENDING,
        verification_method=AbhaLink.VerificationMethod.MOBILE_OTP,
    )
    assert auth_client.get(f"/api/v1/abdm/abha-links/{theirs.id}/").status_code == 404


@pytest.mark.django_db
def test_consent_request_isolation(auth_client, other_hospital, other_patient):
    other_link = AbhaLink.objects.create(
        hospital=other_hospital, patient=other_patient, status=AbhaLink.Status.LINKED,
        verification_method=AbhaLink.VerificationMethod.MOBILE_OTP, abha_address="theirs@abdm",
    )
    theirs = ConsentRequest.objects.create(
        hospital=other_hospital, patient=other_patient, abha_link=other_link,
        date_range_from=datetime.date(2026, 1, 1), date_range_to=datetime.date(2026, 6, 1),
    )
    assert auth_client.get(f"/api/v1/abdm/consent-requests/{theirs.id}/").status_code == 404


# --- RBAC: "abdm" is only granted to specific templates (see
# apps.accounts.permission_templates) — not every role gets it. ------------

@pytest.mark.django_db
def test_restricted_role_cannot_read_abdm_endpoints(restricted_client, hospital, patient):
    """restricted_client carries the Telephony Operator template, whose
    permission_templates entry has no "abdm" key at all."""
    link = AbhaLink.objects.create(
        hospital=hospital, patient=patient, status=AbhaLink.Status.LINKED,
        verification_method=AbhaLink.VerificationMethod.MOBILE_OTP, abha_address="asha@abdm",
    )
    assert restricted_client.get("/api/v1/abdm/abha-links/").status_code == 403
    assert restricted_client.get(f"/api/v1/abdm/abha-links/{link.id}/").status_code == 403
    assert restricted_client.get("/api/v1/abdm/consent-requests/").status_code == 403
    assert restricted_client.get("/api/v1/abdm/nhcx-transactions/").status_code == 403
