import pytest
from django.contrib.auth.models import Permission

from apps.accounts.models import Role
from apps.accounts.role_sync import sync_all_roles, sync_role


def perms(role):
    return set(role.group.permissions.values_list("content_type__app_label", "codename"))


@pytest.mark.django_db
def test_new_role_records_what_its_template_covered(hospital, department):
    role = Role.objects.create(hospital=hospital, department=department, name="Owner", template=Role.Template.OWNER)
    assert ("cathlab", "view_cathprocedure") in perms(role)
    assert "cathlab.cathprocedure" in role.template_synced_models
    assert sync_role(role) == 0  # nothing new


@pytest.mark.django_db
def test_legacy_role_gets_new_features_but_keeps_deliberate_removals(hospital, department):
    role = Role.objects.create(hospital=hospital, department=department, name="Owner", template=Role.Template.OWNER)
    # A role created before the cath lab existed, and customised by an admin:
    # every cath lab permission missing (feature didn't exist) and one patient
    # permission removed on purpose.
    role.group.permissions.remove(*Permission.objects.filter(content_type__app_label="cathlab"))
    role.group.permissions.remove(Permission.objects.get(content_type__app_label="patients", codename="delete_patient"))
    Role.objects.filter(pk=role.pk).update(template_synced_models=[])
    role.refresh_from_db()

    assert sync_all_roles() > 0
    after = perms(role)
    assert ("cathlab", "view_cathprocedure") in after  # new feature granted
    assert ("patients", "delete_patient") not in after  # admin's removal respected

    # From now on the record is kept, so removing a cath lab permission sticks too.
    role.group.permissions.remove(Permission.objects.get(content_type__app_label="cathlab", codename="delete_cathprocedure"))
    role.refresh_from_db()
    sync_role(role)
    assert ("cathlab", "delete_cathprocedure") not in perms(role)


@pytest.mark.django_db
def test_custom_roles_without_a_template_are_untouched(hospital, department):
    role = Role.objects.create(hospital=hospital, department=department, name="Custom")
    assert sync_role(role) == 0 and not perms(role)
