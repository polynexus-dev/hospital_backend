from contextlib import suppress

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Role, User, assign_role
from apps.core.models import Department, Hospital


def _teardown(instance):
    """Best-effort explicit teardown for fixtures below. pytest-django
    already wraps each test in a transaction that's rolled back afterwards
    — that's the real test-isolation guarantee, not this — but callers
    asked for visible, explicit teardown per fixture rather than relying
    solely on the implicit rollback. It's deliberately best-effort: several
    models elsewhere (e.g. Appointment.slot) use on_delete=PROTECT, which
    raises ProtectedError on a cascading delete through this fixture's
    hospital/department even when every protecting row is *also* about to
    be rolled back — that's Django's documented PROTECT behavior, not a
    bug, and conftest has no business knowing every app's FK graph to avoid
    it. Suppress and let the transaction rollback finish the job."""
    with suppress(Exception):
        instance.delete()


@pytest.fixture
def hospital(db):
    hospital = Hospital.objects.create(name="Test Hospital", slug="test-hospital")
    yield hospital
    _teardown(hospital)


@pytest.fixture
def other_hospital(db):
    other = Hospital.objects.create(name="Other Hospital", slug="other-hospital")
    yield other
    _teardown(other)


@pytest.fixture
def department(hospital):
    dept = Department.objects.create(hospital=hospital, name="OPD")
    yield dept
    _teardown(dept)


@pytest.fixture
def other_department(other_hospital):
    dept = Department.objects.create(hospital=other_hospital, name="OPD")
    yield dept
    _teardown(dept)


@pytest.fixture
def role(hospital, department):
    """template="admin" deliberately — this is the default actor for the
    hundreds of CRUD-mechanics tests across every app, and those tests
    exist to prove create/read/update/delete and tenant isolation work,
    not to re-litigate role permissions on every single one. Dedicated,
    narrowly-scoped RBAC-restriction tests use `restricted_role` /
    `restricted_client` below instead."""
    created = Role.objects.create(hospital=hospital, department=department, name="Front Desk", template=Role.Template.ADMIN)
    yield created
    _teardown(created)


@pytest.fixture
def restricted_role(hospital, department):
    """A genuinely low-privilege role (Telephony Operator template) for
    proving RoleBasedModelPermissions actually restricts something, not
    just that it exists."""
    created = Role.objects.create(hospital=hospital, department=department, name="Telephony Operator", template=Role.Template.TELEPHONY_OPERATOR)
    yield created
    _teardown(created)


@pytest.fixture
def restricted_user(hospital, department, restricted_role):
    created = User.objects.create_user(email="operator@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(created, restricted_role)
    yield created
    _teardown(created)


@pytest.fixture
def restricted_client(api_client, restricted_user):
    api_client.force_authenticate(user=restricted_user)
    yield api_client
    api_client.force_authenticate(user=None)


@pytest.fixture
def user(hospital, department, role):
    created = User.objects.create_user(email="frontdesk@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(created, role)
    yield created
    _teardown(created)


@pytest.fixture
def other_user(other_hospital, other_department):
    """A user belonging to a *different* hospital — the standard actor for
    cross-tenant isolation tests (create data as this user / for this
    hospital, then assert `user` from the `hospital` fixture can't see it)."""
    created = User.objects.create_user(
        email="frontdesk@other-hospital.example", password="testpass123",
        hospital=other_hospital, department=other_department,
    )
    yield created
    _teardown(created)


@pytest.fixture
def staff_user(hospital, department):
    """is_staff=True, for IsAdminUser-gated endpoints (AuditLogViewSet,
    IntegrationHealthView) and the X-Hospital-Id cross-tenant header path."""
    created = User.objects.create_user(
        email="ops@polynexus.in", password="testpass123",
        hospital=hospital, department=department, is_staff=True,
    )
    yield created
    _teardown(created)


@pytest.fixture
def api_client():
    client = APIClient()
    yield client


@pytest.fixture
def auth_client(api_client, user):
    """An APIClient already authenticated as `user` (hospital-scoped,
    non-staff) — the default actor for API-level CRUD tests."""
    api_client.force_authenticate(user=user)
    yield api_client
    api_client.force_authenticate(user=None)
