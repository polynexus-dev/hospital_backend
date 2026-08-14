import pytest
from django.contrib.auth.models import Permission

from apps.accounts.models import Role, User, assign_role


@pytest.mark.django_db
def test_role_creation_backs_a_django_group(hospital):
    role = Role.objects.create(hospital=hospital, name="PRO")
    assert role.group_id is not None
    assert role.group.name == f"{hospital.id}:PRO"


@pytest.mark.django_db
def test_assign_role_syncs_group_membership_and_permissions(hospital, department, user):
    other_role = Role.objects.create(hospital=hospital, department=department, name="Billing Desk")
    permission = Permission.objects.filter(codename="add_department").first()
    if permission:
        other_role.permissions.add(permission)

    assign_role(user, other_role)

    user.refresh_from_db()
    assert user.role_id == other_role.id
    assert other_role.group in user.groups.all()
    if permission:
        assert user.has_perm("core.add_department")


@pytest.mark.django_db
def test_assign_role_removes_previous_group_membership(hospital, department, user, role):
    other_role = Role.objects.create(hospital=hospital, department=department, name="Billing Desk")

    assign_role(user, other_role)

    assert role.group not in user.groups.all()
    assert other_role.group in user.groups.all()


# --- Auth flow (login/refresh) ------------------------------------------

@pytest.mark.django_db
def test_login_with_correct_credentials_returns_a_token_pair(api_client, hospital, department):
    User.objects.create_user(email="login@test-hospital.example", password="correct-horse-1", hospital=hospital, department=department)

    response = api_client.post("/api/v1/auth/login/", {"email": "login@test-hospital.example", "password": "correct-horse-1"}, format="json")

    assert response.status_code == 200
    assert "access" in response.data and "refresh" in response.data


@pytest.mark.django_db
def test_login_with_wrong_password_is_rejected(api_client, hospital, department):
    User.objects.create_user(email="login2@test-hospital.example", password="correct-horse-1", hospital=hospital, department=department)

    response = api_client.post("/api/v1/auth/login/", {"email": "login2@test-hospital.example", "password": "wrong"}, format="json")

    assert response.status_code == 401


@pytest.mark.django_db
def test_login_is_rate_limited_to_5_per_minute_per_ip(api_client, hospital, department):
    """config.settings.test disables DEFAULT_THROTTLE_CLASSES suite-wide
    (see that file's docstring) specifically so the other five login tests
    in this file don't trip this same limit from the same test-client IP —
    re-enable it just for this one test to prove the limit itself actually
    works, against the real Redis-backed cache (see CACHES in
    config.settings.base).

    Patches HospitalTokenObtainPairView.throttle_classes directly rather
    than using @override_settings(REST_FRAMEWORK=...): DRF's APIView sets
    `throttle_classes = api_settings.DEFAULT_THROTTLE_CLASSES` as a plain
    class attribute at *module import time* (rest_framework/views.py) —
    api_settings itself does reload on the `setting_changed` signal
    override_settings fires, but that stale copy on APIView (and
    SimpleRateThrottle.THROTTLE_RATES, same pattern) was already bound to
    the old value and is never reassigned. override_settings silently does
    nothing here; this is the actual, reliable way to test it."""
    from django.core.cache import cache
    from rest_framework.throttling import ScopedRateThrottle

    from apps.accounts.views import HospitalTokenObtainPairView

    User.objects.create_user(email="throttle-test@test-hospital.example", password="correct-horse-1", hospital=hospital, department=department)
    cache.clear()

    original_throttle_classes = HospitalTokenObtainPairView.throttle_classes
    HospitalTokenObtainPairView.throttle_classes = [ScopedRateThrottle]
    try:
        for _ in range(5):
            response = api_client.post("/api/v1/auth/login/", {"email": "throttle-test@test-hospital.example", "password": "wrong"}, format="json")
            assert response.status_code == 401  # wrong password, but not throttled yet

        sixth = api_client.post("/api/v1/auth/login/", {"email": "throttle-test@test-hospital.example", "password": "wrong"}, format="json")
        assert sixth.status_code == 429
    finally:
        HospitalTokenObtainPairView.throttle_classes = original_throttle_classes
        cache.clear()


@pytest.mark.django_db
def test_token_refresh_returns_a_new_access_token(api_client, hospital, department):
    User.objects.create_user(email="login3@test-hospital.example", password="correct-horse-1", hospital=hospital, department=department)
    login = api_client.post("/api/v1/auth/login/", {"email": "login3@test-hospital.example", "password": "correct-horse-1"}, format="json")

    response = api_client.post("/api/v1/auth/refresh/", {"refresh": login.data["refresh"]}, format="json")

    assert response.status_code == 200
    assert "access" in response.data


@pytest.mark.django_db
def test_logout_blacklists_the_refresh_token_so_it_can_no_longer_be_used(api_client, hospital, department):
    """Requires rest_framework_simplejwt.token_blacklist in INSTALLED_APPS —
    without it BLACKLIST_AFTER_ROTATION is a silent no-op and a
    stolen/leaked refresh token stays valid for its full lifetime even
    after logout."""
    User.objects.create_user(email="logout-test@test-hospital.example", password="correct-horse-1", hospital=hospital, department=department)
    login = api_client.post("/api/v1/auth/login/", {"email": "logout-test@test-hospital.example", "password": "correct-horse-1"}, format="json")
    refresh_token = login.data["refresh"]

    logout = api_client.post("/api/v1/auth/logout/", {"refresh": refresh_token}, format="json")
    assert logout.status_code == 200

    reuse_attempt = api_client.post("/api/v1/auth/refresh/", {"refresh": refresh_token}, format="json")
    assert reuse_attempt.status_code == 401


# --- Users API: CRUD, isolation, and real gotchas ------------------------

@pytest.mark.django_db
def test_create_user_via_api_requires_only_email(auth_client, hospital):
    response = auth_client.post("/api/v1/users/", {"email": "new-staff@test-hospital.example"}, format="json")

    assert response.status_code == 201
    created = User.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id


@pytest.mark.django_db
def test_create_user_via_api_ignores_a_spoofed_hospital_in_the_body(auth_client, hospital, other_hospital):
    response = auth_client.post("/api/v1/users/", {"email": "spoof-attempt@test-hospital.example", "hospital": str(other_hospital.id)}, format="json")

    assert response.status_code == 201
    created = User.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id  # not other_hospital — perform_create always wins


@pytest.mark.django_db
def test_user_created_via_api_cannot_log_in_until_a_password_is_set(auth_client, api_client, hospital):
    """Documents a real gotcha: `password` isn't in UserSerializer.Meta.fields
    at all, so POST /api/v1/users/ calls plain User.objects.create(**data) —
    not create_user() — leaving an unusable password hash. There is no
    password field on this endpoint; the only way to set one is
    change_password (self-service) or the ORM directly."""
    response = auth_client.post("/api/v1/users/", {"email": "no-password-yet@test-hospital.example"}, format="json")
    assert response.status_code == 201

    login = api_client.post("/api/v1/auth/login/", {"email": "no-password-yet@test-hospital.example", "password": "any-guess-at-all"}, format="json")
    assert login.status_code == 401


@pytest.mark.django_db
def test_users_list_is_scoped_to_the_authenticated_users_hospital(auth_client, hospital, other_hospital, department, other_department, user):
    User.objects.create_user(email="other-hospital-staff@example.com", password="testpass123", hospital=other_hospital, department=other_department)

    response = auth_client.get("/api/v1/users/")

    emails = {row["email"] for row in response.data["results"]}
    assert user.email in emails
    assert "other-hospital-staff@example.com" not in emails


@pytest.mark.django_db
def test_staff_user_sees_every_hospitals_users(api_client, staff_user, other_hospital, other_department):
    User.objects.create_user(email="other-hospital-staff2@example.com", password="testpass123", hospital=other_hospital, department=other_department)
    api_client.force_authenticate(user=staff_user)

    response = api_client.get("/api/v1/users/")

    emails = {row["email"] for row in response.data["results"]}
    assert "other-hospital-staff2@example.com" in emails


@pytest.mark.django_db
def test_users_me_returns_the_authenticated_user(auth_client, user):
    response = auth_client.get("/api/v1/users/me/")
    assert response.status_code == 200
    assert response.data["email"] == user.email


@pytest.mark.django_db
def test_change_password_rejects_wrong_old_password(auth_client, user):
    response = auth_client.post("/api/v1/users/change_password/", {"old_password": "wrong", "new_password": "newpass1234"}, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_change_password_succeeds_and_new_password_logs_in(auth_client, api_client, user):
    response = auth_client.post("/api/v1/users/change_password/", {"old_password": "testpass123", "new_password": "brand-new-pass-1"}, format="json")
    assert response.status_code == 204

    login = api_client.post("/api/v1/auth/login/", {"email": user.email, "password": "brand-new-pass-1"}, format="json")
    assert login.status_code == 200


# --- switch-hospital: security regression -------------------------------
#
# This action used to accept ANY authenticated user and reassign their
# `hospital` FK to ANY active hospital's id, with no check that the two
# hospitals were related in any way (there's no group/ownership concept on
# Hospital at all) — a non-staff front-desk user could self-escalate into a
# completely unrelated hospital's full data access. Verified empirically
# before fixing (a real request round-trip, not a theoretical read of the
# code), then fixed by requiring is_staff. These tests pin that fix down.

@pytest.mark.django_db
def test_non_staff_user_cannot_switch_hospital(auth_client, other_hospital):
    response = auth_client.post("/api/v1/users/switch-hospital/", {"hospital_id": str(other_hospital.id)}, format="json")
    assert response.status_code == 403


@pytest.mark.django_db
def test_non_staff_users_hospital_is_unchanged_after_a_rejected_switch_attempt(auth_client, user, hospital, other_hospital):
    auth_client.post("/api/v1/users/switch-hospital/", {"hospital_id": str(other_hospital.id)}, format="json")
    user.refresh_from_db()
    assert user.hospital_id == hospital.id


@pytest.mark.django_db
def test_staff_user_can_switch_hospital(api_client, staff_user, other_hospital):
    api_client.force_authenticate(user=staff_user)

    response = api_client.post("/api/v1/users/switch-hospital/", {"hospital_id": str(other_hospital.id)}, format="json")

    assert response.status_code == 200
    staff_user.refresh_from_db()
    assert staff_user.hospital_id == other_hospital.id


@pytest.mark.django_db
def test_switch_hospital_404s_for_an_unknown_hospital_id(api_client, staff_user):
    import uuid
    api_client.force_authenticate(user=staff_user)

    response = api_client.post("/api/v1/users/switch-hospital/", {"hospital_id": str(uuid.uuid4())}, format="json")

    assert response.status_code == 404


@pytest.mark.django_db
def test_switch_hospital_400s_when_hospital_id_is_missing(api_client, staff_user):
    api_client.force_authenticate(user=staff_user)
    response = api_client.post("/api/v1/users/switch-hospital/", {}, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_patching_own_user_record_cannot_bypass_switch_hospital_via_the_hospital_field(auth_client, user, hospital, other_hospital):
    """The second door on the same bug: `hospital` used to be a writable
    field on UserSerializer, so PATCH /users/{own id}/ with a different
    hospital id worked even for non-staff — completely bypassing
    switch-hospital's is_staff gate (verified empirically: a Front Desk
    role with ordinary change_user permission could self-escalate this
    way even after that gate was added). Now read-only, so this either
    silently ignores the field or 200s without applying it — either way,
    the user's hospital must not actually change."""
    response = auth_client.patch(f"/api/v1/users/{user.id}/", {"hospital": str(other_hospital.id)}, format="json")

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.hospital_id == hospital.id


# --- available_hospitals: information-disclosure regression --------------
#
# Companion to the switch-hospital fix above — UserSerializer used to list
# every active hospital on the platform (name/slug/city) to every logged-in
# user regardless of role, which is what fed the switch-hospital UI in the
# first place. Non-staff now only see their own hospital in this list.

@pytest.mark.django_db
def test_non_staff_users_available_hospitals_excludes_other_hospitals(auth_client, hospital, other_hospital):
    response = auth_client.get("/api/v1/users/me/")

    names = {row["name"] for row in response.data["available_hospitals"]}
    assert names == {hospital.name}
    assert other_hospital.name not in names


@pytest.mark.django_db
def test_staff_users_available_hospitals_includes_every_active_hospital(api_client, staff_user, hospital, other_hospital):
    api_client.force_authenticate(user=staff_user)

    response = api_client.get("/api/v1/users/me/")

    names = {row["name"] for row in response.data["available_hospitals"]}
    assert names == {hospital.name, other_hospital.name}


# --- Users/Roles: retrieve/update/destroy isolation -----------------------

@pytest.mark.django_db
def test_user_retrieve_404s_for_another_hospitals_user(auth_client, other_hospital, other_department):
    theirs = User.objects.create_user(email="theirs@other-hospital.example", password="testpass123", hospital=other_hospital, department=other_department)
    response = auth_client.get(f"/api/v1/users/{theirs.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_role_crud_and_isolation(auth_client, hospital, other_hospital, other_department):
    create = auth_client.post("/api/v1/roles/", {"name": "PRO"}, format="json")
    assert create.status_code == 201
    created = Role.objects.get(pk=create.data["id"])
    assert created.hospital_id == hospital.id

    update = auth_client.patch(f"/api/v1/roles/{created.id}/", {"description": "Public relations officer"}, format="json")
    assert update.status_code == 200
    created.refresh_from_db()
    assert created.description == "Public relations officer"

    theirs = Role.objects.create(hospital=other_hospital, department=other_department, name="Their Role")
    assert auth_client.get(f"/api/v1/roles/{theirs.id}/").status_code == 404

    delete = auth_client.delete(f"/api/v1/roles/{created.id}/")
    assert delete.status_code == 204
    assert not Role.objects.filter(pk=created.id).exists()
