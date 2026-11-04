"""Regression tests for the C1–C5 authorization fixes.

C1  mobile OTP must not echo the code in the response body
C2  an OTP-minted patient token is refused on the staff API, accepted on the
    patient surface
C3  assign-role honours the role-assignment ceiling
C5  set-password / block honour the target's privilege tier
C4  (privacy slice) data-rights erasure actions are gated, not open to any
    authenticated hospital user
"""
import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Role, User, assign_role


def _staff(hospital, department, email, template):
    u = User.objects.create_user(email=email, password="Str0ng!Passw0rd#1", hospital=hospital, department=department)
    r = Role.objects.create(hospital=hospital, department=department, name=template, template=template)
    assign_role(u, r)
    return u, r


def _client(u):
    c = APIClient()
    c.force_authenticate(user=u)
    return c


# ---- C1 -------------------------------------------------------------------

@pytest.mark.django_db
def test_c1_send_otp_does_not_leak_the_code(hospital):
    res = APIClient().post("/api/v1/mobile/auth/otp/send/", {"mobile": "9876500011", "hospital": hospital.slug}, format="json")
    assert res.status_code == 200, res.content
    assert "otp" not in res.json()


# ---- C2 -------------------------------------------------------------------

@pytest.mark.django_db
def test_c2_patient_token_is_confined_to_the_patient_surface(hospital, department):
    from apps.patients.registration import issue_otp

    _staff(hospital, department, "dr@h.example", "doctor")  # someone in the staff directory
    mobile = "9876500022"
    code = issue_otp(hospital, mobile)  # the code is delivered by SMS; we read it directly only in the test
    verify = APIClient().post("/api/v1/mobile/auth/otp/verify/",
                              {"mobile": mobile, "otp": code, "hospital": hospital.slug}, format="json")
    assert verify.status_code == 200, verify.content
    access = verify.json()["access"]

    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    # Accepted on the patient surface it was minted for.
    assert c.get("/api/v1/mobile/me/").status_code == 200
    # Refused on the staff API (no baseline staff-directory read).
    assert c.get("/api/v1/users/").status_code == 401


# ---- C3 -------------------------------------------------------------------

@pytest.mark.django_db
def test_c3_assign_role_cannot_grant_an_admin_role(hospital, department):
    hr, _ = _staff(hospital, department, "hr@h.example", "hr_manager")
    owner, owner_role = _staff(hospital, department, "owner@h.example", "owner")
    res = _client(hr).post(f"/api/v1/users/{hr.pk}/assign-role/", {"role_id": owner_role.pk}, format="json")
    assert res.status_code == 403
    hr.refresh_from_db()
    assert hr.role.template == "hr_manager"  # unchanged


# ---- C5 -------------------------------------------------------------------

@pytest.mark.django_db
def test_c5_cannot_reset_or_block_a_higher_tier_account(hospital, department):
    hr, _ = _staff(hospital, department, "hr2@h.example", "hr_manager")
    owner, _ = _staff(hospital, department, "owner2@h.example", "owner")
    c = _client(hr)
    assert c.post(f"/api/v1/users/{owner.pk}/set-password/", {"new_password": "Wh4tever!New#9"}, format="json").status_code == 403
    assert c.post(f"/api/v1/users/{owner.pk}/block/", {"reason": "x"}, format="json").status_code == 403
    owner.refresh_from_db()
    assert owner.is_blocked is False
    assert owner.check_password("Str0ng!Passw0rd#1")  # password untouched


@pytest.mark.django_db
def test_c5_admin_can_still_manage_ordinary_staff(hospital, department):
    admin, _ = _staff(hospital, department, "admin@h.example", "admin")
    clerk, _ = _staff(hospital, department, "clerk@h.example", "telephony_operator")
    res = _client(admin).post(f"/api/v1/users/{clerk.pk}/set-password/", {"new_password": "Fresh!Clerk#Pw9"}, format="json")
    assert res.status_code == 204, res.content


# ---- C4 (privacy slice) ---------------------------------------------------

@pytest.mark.django_db
def test_c4_erasure_action_is_denied_to_a_non_privacy_role(hospital, department):
    from apps.patients.models import Patient
    from apps.privacy.models import DataRightsRequest

    clerk, _ = _staff(hospital, department, "op@h.example", "telephony_operator")
    patient = Patient.objects.create(hospital=hospital, first_name="P", mobile="9876500033")
    req = DataRightsRequest.objects.create(hospital=hospital, patient=patient, request_type="erasure")
    res = _client(clerk).post(f"/api/v1/privacy/data-rights-requests/{req.pk}/complete/", {}, format="json")
    assert res.status_code == 403
    assert DataRightsRequest.objects.get(pk=req.pk).status == req.status  # not completed


@pytest.mark.django_db
def test_c4_erasure_action_still_works_for_an_admin(hospital, department):
    from apps.patients.models import Patient
    from apps.privacy.models import DataRightsRequest

    admin, _ = _staff(hospital, department, "admin2@h.example", "admin")
    patient = Patient.objects.create(hospital=hospital, first_name="P", mobile="9876500044")
    req = DataRightsRequest.objects.create(hospital=hospital, patient=patient, request_type="access", status=DataRightsRequest.Status.VERIFIED)
    res = _client(admin).post(f"/api/v1/privacy/data-rights-requests/{req.pk}/complete/", {}, format="json")
    assert res.status_code == 200, res.content
