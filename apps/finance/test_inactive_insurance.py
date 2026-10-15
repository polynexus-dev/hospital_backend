"""MedNet M2.22: patient-linked insurance can be made inactive; inactive
policies are hidden (except for finance.manage_inactive_insurance), can't be
used at the OPD desk or as a bill's payer, and can be reactivated."""
from datetime import date

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Role, User, assign_role
from apps.billing.models import Bill
from apps.finance.models import InsurancePolicy
from apps.patients.models import Patient
from apps.tpa.models import TPACompany
from apps.tpa.non_payable import payer_for

P = "/api/v1/finance/insurance-policies/"


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Meera", last_name="Kale", mobile="+919000000701", date_of_birth=date(1975, 5, 5))


@pytest.fixture
def tpa(hospital):
    return TPACompany.objects.create(hospital=hospital, name="Star Health")


@pytest.fixture
def policy(hospital, patient, tpa):
    return InsurancePolicy.objects.create(hospital=hospital, patient=patient, tpa_company=tpa, insurer="Star Health", policy_number="SH-1001")


def client_for(hospital, department, template, email):
    role = Role.objects.create(hospital=hospital, department=department, name=email, template=template)
    u = User.objects.create_user(email=email, password="x", hospital=hospital, department=department)
    assign_role(u, role)
    c = APIClient()
    c.force_authenticate(u)
    return c


def test_deactivate_needs_a_reason_and_hides_the_policy(auth_client, patient, policy, tpa):
    assert auth_client.post(f"{P}{policy.pk}/deactivate/", {}, format="json").status_code == 400
    r = auth_client.post(f"{P}{policy.pk}/deactivate/", {"reason": "Policy lapsed"}, format="json")
    assert r.status_code == 200 and r.data["is_active"] is False and r.data["deactivation_reason"] == "Policy lapsed"
    assert auth_client.get(f"{P}?patient={patient.pk}").data["count"] == 0  # hidden by default
    listed = auth_client.get(f"{P}?patient={patient.pk}&include_inactive=1").data
    assert listed["count"] == 1 and listed["results"][0]["deactivated_by_name"]
    bill = Bill.objects.create(hospital=patient.hospital, patient=patient)
    assert payer_for(bill) is None  # no longer the bill's payer


def test_reactivate(auth_client, policy):
    auth_client.post(f"{P}{policy.pk}/deactivate/", {"reason": "Wrong card"}, format="json")
    r = auth_client.post(f"{P}{policy.pk}/activate/")
    assert r.status_code == 200 and r.data["is_active"] is True and r.data["deactivation_reason"] == ""


def test_users_without_the_permission_never_see_inactive_policies(hospital, department, patient, policy):
    from django.contrib.auth.models import Permission

    InsurancePolicy.objects.filter(pk=policy.pk).update(is_active=False)
    billing = client_for(hospital, department, Role.Template.BILLING_EXECUTIVE, "bill.ins@test-hospital.example")
    u = User.objects.get(email="bill.ins@test-hospital.example")
    assert not u.has_perm("finance.manage_inactive_insurance") and u.has_perm("finance.view_insurancepolicy")
    assert billing.get(f"{P}?patient={patient.pk}&include_inactive=1").data["count"] == 0
    assert billing.post(f"{P}{policy.pk}/activate/").status_code in (403, 404)
    assert Permission.objects.filter(codename="manage_inactive_insurance").exists()
