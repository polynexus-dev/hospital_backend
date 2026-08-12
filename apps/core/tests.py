import pytest

from apps.core.models import Department
from apps.core.tenancy import tenant_context


@pytest.mark.django_db
def test_tenant_manager_scopes_queryset_to_current_hospital(hospital, other_hospital):
    Department.objects.create(hospital=hospital, name="OPD")
    Department.objects.create(hospital=other_hospital, name="IPD")

    with tenant_context(hospital.id):
        names = set(Department.objects.values_list("name", flat=True))
    assert names == {"OPD"}

    with tenant_context(other_hospital.id):
        names = set(Department.objects.values_list("name", flat=True))
    assert names == {"IPD"}


@pytest.mark.django_db
def test_tenant_manager_is_unscoped_outside_request_context(hospital, other_hospital):
    Department.objects.create(hospital=hospital, name="OPD")
    Department.objects.create(hospital=other_hospital, name="IPD")

    # No tenant_context set — management commands / migrations / Celery
    # beat need to see everything.
    assert Department.objects.count() == 2
