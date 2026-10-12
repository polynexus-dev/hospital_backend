"""DPDP Act 2023 consent register: notices per purpose, consent and its withdrawal.
DPDP Act 2023 s.5-6 (notice and consent); supports NABH DAC.2.b.
"""
import pytest

from apps.communications.models import ConsentOptOut
from apps.communications.services import is_opted_out
from apps.patients.models import Patient
from apps.privacy.models import ConsentNotice, PatientConsent
from apps.privacy.services import has_consent


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Asha", last_name="Rao", mobile="9876500011")


def publish(client, purpose="marketing", body="We send health camp offers by SMS and WhatsApp. Withdraw any time at the front desk."):
    res = client.post("/api/v1/privacy/consent-notices/", {"purpose": purpose, "title": "Offers and camps", "body": body}, format="json")
    assert res.status_code == 201, res.data
    return res.data


@pytest.mark.django_db
def test_notices_are_versioned_and_never_rewritten(auth_client):
    v1 = publish(auth_client)
    v2 = publish(auth_client, body="Updated wording.")
    assert (v1["version"], v2["version"]) == (1, 2)
    assert not ConsentNotice.objects.get(pk=v1["id"]).is_active and ConsentNotice.objects.get(pk=v2["id"]).is_active
    res = auth_client.patch(f"/api/v1/privacy/consent-notices/{v2['id']}/", {"body": "Sneaky edit"}, format="json")
    assert res.status_code == 400
    assert auth_client.delete(f"/api/v1/privacy/consent-notices/{v2['id']}/").status_code == 405


@pytest.mark.django_db
def test_consent_then_withdrawal_switches_messages_off(auth_client, patient, user):
    notice = publish(auth_client)
    res = auth_client.post("/api/v1/privacy/consents/", {"patient": patient.pk, "notice": notice["id"], "status": "granted", "method": "written"}, format="json")
    assert res.status_code == 201, res.data
    assert res.data["purpose"] == "marketing" and res.data["captured_by"] == user.pk
    assert has_consent(patient, "marketing") and not is_opted_out(patient, "whatsapp", ConsentOptOut.Purpose.MARKETING)

    withdrawn = auth_client.post(f"/api/v1/privacy/consents/{res.data['id']}/withdraw/", {"reason": "Too many messages"}, format="json")
    assert withdrawn.status_code == 200 and withdrawn.data["status"] == "withdrawn" and withdrawn.data["withdrawn_at"]
    assert not has_consent(patient, "marketing")
    assert is_opted_out(patient, "whatsapp", ConsentOptOut.Purpose.MARKETING) and is_opted_out(patient, "sms", ConsentOptOut.Purpose.MARKETING)
    # A consent can't be edited or deleted, only withdrawn.
    assert auth_client.patch(f"/api/v1/privacy/consents/{res.data['id']}/", {"status": "granted"}, format="json").status_code == 405
    assert auth_client.delete(f"/api/v1/privacy/consents/{res.data['id']}/").status_code == 405


@pytest.mark.django_db
def test_summary_shows_every_purpose_and_outdated_consents(auth_client, patient):
    v1 = publish(auth_client)
    auth_client.post("/api/v1/privacy/consents/", {"patient": patient.pk, "notice": v1["id"], "status": "granted"}, format="json")
    publish(auth_client, body="New wording")  # the patient agreed to v1
    rows = {r["purpose"]: r for r in auth_client.get(f"/api/v1/privacy/consents/summary/?patient={patient.pk}").data}
    assert set(rows) >= {"treatment", "marketing", "reminders", "research", "abdm_sharing"}
    assert rows["marketing"]["outdated"] is True and rows["marketing"]["current_notice"]["version"] == 2
    assert rows["research"]["consent"] is None


@pytest.mark.django_db
def test_guardian_consent_needs_a_name_and_old_notices_are_refused(auth_client, patient):
    v1 = publish(auth_client, purpose="research")
    publish(auth_client, purpose="research", body="v2")
    old = auth_client.post("/api/v1/privacy/consents/", {"patient": patient.pk, "notice": v1["id"], "status": "granted"}, format="json")
    assert old.status_code == 400 and "replaced" in str(old.data["notice"])
    current = ConsentNotice.objects.get(purpose="research", is_active=True)
    res = auth_client.post("/api/v1/privacy/consents/", {"patient": patient.pk, "notice": current.pk, "status": "granted", "given_by": "guardian"}, format="json")
    assert res.status_code == 400 and "guardian_name" in res.data
    assert not PatientConsent.objects.exists()


@pytest.mark.django_db
def test_staff_without_patient_rights_cannot_record_consent(auth_client, patient, hospital, department):
    from rest_framework.test import APIClient

    from apps.accounts.models import Role, User, assign_role

    notice = publish(auth_client, purpose="treatment")
    hr = User.objects.create_user(email="hr@test-hospital.example", password="x", hospital=hospital, department=department)
    assign_role(hr, Role.objects.create(hospital=hospital, department=department, name="HR", template=Role.Template.HR_MANAGER))
    client = APIClient()
    client.force_authenticate(user=hr)
    res = client.post("/api/v1/privacy/consents/", {"patient": patient.pk, "notice": notice["id"], "status": "granted"}, format="json")
    assert res.status_code == 403
    assert client.get(f"/api/v1/privacy/consents/summary/?patient={patient.pk}").status_code == 403
