import pytest

from apps.accounts.models import Role, User, assign_role
from apps.core.models import Department, Hospital


@pytest.fixture
def hospital(db):
    return Hospital.objects.create(name="Test Hospital", slug="test-hospital")


@pytest.fixture
def other_hospital(db):
    return Hospital.objects.create(name="Other Hospital", slug="other-hospital")


@pytest.fixture
def department(hospital):
    return Department.objects.create(hospital=hospital, name="OPD")


@pytest.fixture
def role(hospital, department):
    return Role.objects.create(hospital=hospital, department=department, name="Front Desk")


@pytest.fixture
def user(hospital, department, role):
    created = User.objects.create_user(email="frontdesk@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(created, role)
    return created
