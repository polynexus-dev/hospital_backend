import pytest
from django.contrib.auth.models import Permission
from rest_framework.test import APIClient

from apps.accounts.models import Role, User, assign_role
from apps.accounts.permission_catalog import catalog_codes


def perm(code):
    app, codename = code.split(".")
    return Permission.objects.get(content_type__app_label=app, codename=codename)


@pytest.fixture
def saas_client(db):
    """Its own APIClient: tests use it alongside auth_client."""
    client = APIClient()
    client.force_authenticate(user=User.objects.create_user(email="owner@platform.example", password="x", is_saas_admin=True))
    return client


@pytest.fixture
def manager(hospital, department):
    """A non-admin who may edit roles but holds only a few permissions."""
    role = Role.objects.create(hospital=hospital, department=department, name="Ward Manager")
    role.group.permissions.add(*(perm(c) for c in ["accounts.change_role", "accounts.view_role", "patients.view_patient", "patients.add_patient"]))
    created = User.objects.create_user(email="manager@test-hospital.example", password="x", hospital=hospital, department=department)
    assign_role(created, role)
    return created


@pytest.fixture
def blank_role(hospital, department):
    return Role.objects.create(hospital=hospital, department=department, name="Custom")


def fresh(user):
    return User.objects.get(pk=user.pk)  # drops the per-object permission caches


@pytest.mark.django_db
def test_saas_ceiling_binds_hospital_admin_and_staff(saas_client, auth_client, user, restricted_user, hospital):
    assert fresh(user).has_perm("accounts.view_role")
    everything = sorted(catalog_codes() - {"accounts.view_role", "telephony.add_call"})
    res = saas_client.put(f"/api/v1/saas-admin/hospitals/{hospital.pk}/permissions/", {"permissions": everything}, format="json")
    assert res.status_code == 200 and res.data["restricted"]

    assert not fresh(user).has_perm("accounts.view_role")  # admin role, but the plan removed it
    auth_client.force_authenticate(user=fresh(user))  # as a real request would load it
    assert auth_client.get("/api/v1/roles/").status_code == 403  # reads are bound by the ceiling too
    assert not fresh(restricted_user).has_perm("telephony.add_call")

    saas_client.put(f"/api/v1/saas-admin/hospitals/{hospital.pk}/permissions/", {"permissions": None}, format="json")
    auth_client.force_authenticate(user=fresh(user))
    assert auth_client.get("/api/v1/roles/").status_code == 200
    assert fresh(restricted_user).has_perm("telephony.add_call")  # lifting it restores what the role had


@pytest.mark.django_db
def test_hospital_cannot_set_its_own_ceiling(auth_client, hospital):
    res = auth_client.put(f"/api/v1/saas-admin/hospitals/{hospital.pk}/permissions/", {"permissions": None}, format="json")
    assert res.status_code == 403


@pytest.mark.django_db
def test_admin_grants_within_ceiling_only(saas_client, auth_client, hospital, blank_role):
    saas_client.put(f"/api/v1/saas-admin/hospitals/{hospital.pk}/permissions/",
                    {"permissions": sorted(catalog_codes() - {"patients.delete_patient"})}, format="json")
    url = f"/api/v1/roles/{blank_role.pk}/permissions/"

    res = auth_client.get(url)
    assert "patients.delete_patient" not in res.data["grantable"]

    res = auth_client.put(url, {"permissions": ["patients.view_patient", "patients.delete_patient"]}, format="json")
    assert res.status_code == 400

    res = auth_client.put(url, {"permissions": ["patients.view_patient", "patients.change_patient"]}, format="json")
    assert res.status_code == 200
    assert res.data["selected"] == ["patients.change_patient", "patients.view_patient"]


@pytest.mark.django_db
def test_non_admin_grants_only_what_they_hold_and_keeps_the_rest(api_client, manager, blank_role):
    blank_role.group.permissions.add(perm("billing.view_bill"))  # given earlier by the hospital admin
    api_client.force_authenticate(user=manager)
    url = f"/api/v1/roles/{blank_role.pk}/permissions/"

    res = api_client.put(url, {"permissions": ["patients.view_patient", "patients.delete_patient"]}, format="json")
    assert res.status_code == 400  # manager doesn't hold delete_patient

    res = api_client.put(url, {"permissions": ["patients.view_patient"]}, format="json")
    assert res.status_code == 200
    held = {f"{p.content_type.app_label}.{p.codename}" for p in blank_role.group.permissions.all()}
    assert held == {"patients.view_patient", "billing.view_bill"}  # beyond their reach: untouched


@pytest.mark.django_db
def test_admin_template_roles_are_locked(auth_client, role):
    res = auth_client.put(f"/api/v1/roles/{role.pk}/permissions/", {"permissions": []}, format="json")
    assert res.status_code == 403
    assert auth_client.get(f"/api/v1/roles/{role.pk}/permissions/").data["locked_reason"]


@pytest.mark.django_db
def test_direct_user_permissions(auth_client, restricted_user):
    url = f"/api/v1/users/{restricted_user.pk}/permissions/"
    assert not fresh(restricted_user).has_perm("patients.delete_patient")

    res = auth_client.put(url, {"permissions": ["patients.delete_patient"]}, format="json")
    assert res.status_code == 200
    assert res.data["selected"] == ["patients.delete_patient"]
    assert res.data["inherited"]  # the role's own permissions, shown read-only
    assert fresh(restricted_user).has_perm("patients.delete_patient")


@pytest.mark.django_db
def test_staff_without_change_permission_cannot_edit(restricted_client, blank_role):
    res = restricted_client.put(f"/api/v1/roles/{blank_role.pk}/permissions/", {"permissions": []}, format="json")
    assert res.status_code == 403


# --- reads need `view` ------------------------------------------------------

@pytest.mark.django_db
def test_reads_require_view_permission(api_client, hospital, department):
    role = Role.objects.create(hospital=hospital, department=department, name="Billing Clerk")
    role.group.permissions.add(perm("billing.view_bill"))
    clerk = User.objects.create_user(email="clerk@test-hospital.example", password="x", hospital=hospital, department=department)
    assign_role(clerk, role)
    api_client.force_authenticate(user=clerk)

    assert api_client.get("/api/v1/roles/").status_code == 403  # no accounts.view_role
    role.group.permissions.add(perm("accounts.view_role"))
    api_client.force_authenticate(user=fresh(clerk))
    assert api_client.get("/api/v1/roles/").status_code == 200


@pytest.mark.django_db
def test_templates_start_with_baseline_reads(restricted_user):
    u = fresh(restricted_user)
    assert u.has_perm("core.view_department") and u.has_perm("appointments.view_doctor")


# --- assigning roles --------------------------------------------------------

@pytest.mark.django_db
def test_non_admin_cannot_assign_role_beyond_their_access(api_client, manager, restricted_user, blank_role, hospital):
    manager.role.group.permissions.add(perm("accounts.change_user"))
    api_client.force_authenticate(user=fresh(manager))
    url = f"/api/v1/users/{restricted_user.pk}/"

    blank_role.group.permissions.add(perm("billing.view_bill"))  # manager doesn't hold this
    assert api_client.patch(url, {"role": blank_role.pk}, format="json").status_code == 403
    assert api_client.get("/api/v1/roles/").data["results"][0]["assignable"] in (True, False)

    blank_role.group.permissions.set([perm("patients.view_patient")])  # manager does
    assert api_client.patch(url, {"role": blank_role.pk}, format="json").status_code == 200


@pytest.mark.django_db
def test_only_hospital_admins_handle_admin_roles(api_client, manager, user, role, restricted_user):
    manager.role.group.permissions.add(perm("accounts.change_user"))
    api_client.force_authenticate(user=fresh(manager))
    assert api_client.patch(f"/api/v1/users/{restricted_user.pk}/", {"role": role.pk}, format="json").status_code == 403  # admin role
    assert api_client.patch(f"/api/v1/users/{user.pk}/", {"is_active": False}, format="json").status_code == 403  # an admin's account


@pytest.mark.django_db
def test_cannot_assign_another_hospitals_role(auth_client, restricted_user, other_hospital, other_department):
    foreign = Role.objects.create(hospital=other_hospital, department=other_department, name="Foreign")
    assert auth_client.patch(f"/api/v1/users/{restricted_user.pk}/", {"role": foreign.pk}, format="json").status_code == 403
