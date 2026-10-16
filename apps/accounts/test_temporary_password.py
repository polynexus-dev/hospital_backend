"""Admin-issued logins for every role: a one-time temporary password the user
must replace at first sign-in (DOM.4.a/d), and the doctor-profile shortcut."""
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.appointments.models import Doctor

NEW_PASSWORD = "Sunrise#Ward42"


def login(email, password):
    return APIClient().post("/api/v1/auth/login/", {"email": email, "password": password}, format="json")


def test_add_user_of_any_role_then_first_sign_in_forces_a_new_password(auth_client, hospital, department):
    nurse_role = Role.objects.create(hospital=hospital, name="Staff Nurse", template=Role.Template.NURSE)
    res = auth_client.post("/api/v1/users/", {"email": "new.nurse@test-hospital.example", "first_name": "Neha", "role": nurse_role.pk, "department": department.pk}, format="json")
    assert res.status_code == 201, res.content
    user_id = res.json()["id"]
    assert login("new.nurse@test-hospital.example", "anything").status_code != 200  # no usable password yet

    res = auth_client.post(f"/api/v1/users/{user_id}/temporary-password/")
    assert res.status_code == 200, res.content
    temp = res.json()["temporary_password"]
    assert len(temp) >= 12

    first = login("new.nurse@test-hospital.example", temp)
    assert first.json().get("password_expired") is True and "access" not in first.json()

    change = APIClient().post("/api/v1/auth/password/expired-change/", {"email": "new.nurse@test-hospital.example", "old_password": temp, "new_password": NEW_PASSWORD}, format="json")
    assert change.status_code == 200, change.content
    assert "access" in login("new.nurse@test-hospital.example", NEW_PASSWORD).json()
    assert User.objects.get(pk=user_id).must_change_password is False


def test_reset_password_issues_a_new_temporary_one_and_clears_a_lockout(auth_client, hospital, user):
    from django.utils import timezone

    staff = User.objects.create_user(email="locked@test-hospital.example", password="Old#Password1", hospital=hospital)
    User.objects.filter(pk=staff.pk).update(failed_login_attempts=9, locked_until=timezone.now() + timezone.timedelta(hours=1))
    temp = auth_client.post(f"/api/v1/users/{staff.pk}/temporary-password/").json()["temporary_password"]
    staff.refresh_from_db()
    assert staff.locked_until is None and staff.failed_login_attempts == 0 and staff.must_change_password
    assert login(staff.email, "Old#Password1").status_code != 200  # the old one no longer works
    assert login(staff.email, temp).json().get("password_expired") is True


def test_only_user_admins_can_issue_passwords(restricted_client, hospital):
    staff = User.objects.create_user(email="someone@test-hospital.example", password="Old#Password1", hospital=hospital)
    assert restricted_client.post(f"/api/v1/users/{staff.pk}/temporary-password/").status_code in (403, 404)


def test_doctor_profile_gets_a_login_with_the_consultant_role(auth_client, hospital, department):
    doctor = Doctor.objects.create(hospital=hospital, department=department, name="Anjali Rao", email="anjali.rao@test-hospital.example")
    res = auth_client.post(f"/api/v1/doctors/{doctor.pk}/grant-login/")
    assert res.status_code == 201, res.content
    body = res.json()
    doctor.refresh_from_db()
    assert doctor.user.email == "anjali.rao@test-hospital.example"
    assert doctor.user.role.template == Role.Template.DOCTOR and doctor.user.department_id == department.pk
    assert login(doctor.user.email, body["temporary_password"]).json().get("password_expired") is True

    profile = auth_client.get(f"/api/v1/doctors/{doctor.pk}/").json()
    assert profile["login_email"] == doctor.user.email and profile["login_must_change_password"] is True
    assert auth_client.post(f"/api/v1/doctors/{doctor.pk}/grant-login/").status_code == 400  # already has one


def test_doctor_login_needs_an_unused_email(auth_client, hospital, user):
    no_email = Doctor.objects.create(hospital=hospital, name="No Email")
    assert auth_client.post(f"/api/v1/doctors/{no_email.pk}/grant-login/").status_code == 400
    taken = Doctor.objects.create(hospital=hospital, name="Taken", email=user.email)
    assert auth_client.post(f"/api/v1/doctors/{taken.pk}/grant-login/").status_code == 400


def test_hospital_owner_can_add_users_and_reset_passwords(hospital, department):
    # the owner role (an admin template) manages staff even without accounts.change_user
    owner_role = Role.objects.create(hospital=hospital, name="Hospital Owner", template=Role.Template.OWNER)
    owner = User.objects.create_user(email="owner2@test-hospital.example", password="Owner#Pass123", hospital=hospital)
    from apps.accounts.models import assign_role

    assign_role(owner, owner_role)
    client = APIClient()
    client.force_authenticate(owner)
    staff = User.objects.create_user(email="desk@test-hospital.example", password="Old#Password1", hospital=hospital)
    assert client.post(f"/api/v1/users/{staff.pk}/temporary-password/").status_code == 200
    doctor = Doctor.objects.create(hospital=hospital, name="Owner Made", email="owner.made@test-hospital.example")
    assert client.post(f"/api/v1/doctors/{doctor.pk}/grant-login/").status_code == 201
