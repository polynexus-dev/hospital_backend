import pytest
from django.contrib.auth.models import Permission

from apps.accounts.models import Role, assign_role


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
