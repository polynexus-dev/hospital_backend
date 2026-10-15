"""MedNet M2.22 OT: pre-op investigations ordered from the anaesthetist's
screen, and updated-by shown on OT records."""
from datetime import date

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Role, User, assign_role
from apps.billing.models import Bill
from apps.laboratory.models import LabOrder, LabTest
from apps.ot.models import SurgeryRequest
from apps.patients.models import Patient
from apps.radiology.models import RadiologyOrder, RadiologyProcedure


@pytest.fixture
def req(hospital):
    p = Patient.objects.create(hospital=hospital, first_name="Ravi", last_name="More", mobile="+919000000801", date_of_birth=date(1970, 2, 2))
    return SurgeryRequest.objects.create(hospital=hospital, patient=p, proposed_procedure="Laparoscopic cholecystectomy")


@pytest.fixture
def catalogue(hospital):
    return {
        "cbc": LabTest.objects.create(hospital=hospital, name="CBC", code="CBC", price=300),
        "pt": LabTest.objects.create(hospital=hospital, name="PT/INR", code="PTINR", price=400),
        "cxr": RadiologyProcedure.objects.create(hospital=hospital, name="Chest X-ray", modality="xray", price=500),
    }


@pytest.fixture
def anaesthetist_client(hospital, department):
    role = Role.objects.create(hospital=hospital, department=department, name="Anaesthetist", template=Role.Template.ANAESTHETIST)
    u = User.objects.create_user(email="anaes@test-hospital.example", password="x", hospital=hospital, department=department, first_name="Shweta", last_name="Kapoor")
    assign_role(u, role)
    c = APIClient()
    c.force_authenticate(u)
    return c


def test_anaesthetist_orders_preop_workup_and_sees_it_listed(anaesthetist_client, req, catalogue):
    url = f"/api/v1/ot/surgery-requests/{req.pk}/request-investigations/"
    r = anaesthetist_client.post(url, {"lab_tests": [catalogue["cbc"].pk, catalogue["pt"].pk], "radiology_procedures": [catalogue["cxr"].pk], "notes": "Fit for GA?"}, format="json")
    assert r.status_code == 201, r.data
    assert sorted(x["test"] for x in r.data["lab"]) == ["CBC", "PT/INR"] and r.data["radiology"][0]["test"] == "Chest X-ray"
    order = LabOrder.objects.get(patient=req.patient)
    assert "Pre-anaesthetic work-up for Laparoscopic cholecystectomy" in order.clinical_notes and order.ordered_by.email == "anaes@test-hospital.example"
    assert RadiologyOrder.objects.filter(patient=req.patient, procedure=catalogue["cxr"]).exists()
    listed = anaesthetist_client.get(f"/api/v1/ot/surgery-requests/{req.pk}/investigations/")
    assert listed.status_code == 200 and len(listed.data["lab"]) == 2


def test_preop_workup_needs_something_and_respects_bill_hold(auth_client, req, catalogue):
    url = f"/api/v1/ot/surgery-requests/{req.pk}/request-investigations/"
    assert auth_client.post(url, {"lab_tests": []}, format="json").status_code == 400
    Bill.objects.create(hospital=req.hospital, patient=req.patient, status="unpaid", on_hold=True, hold_reason="Deposit pending")
    r = auth_client.post(url, {"lab_tests": [catalogue["cbc"].pk]}, format="json")
    assert r.status_code == 400 and r.data["code"] == "bill_on_hold"
    assert auth_client.post(url, {"lab_tests": [catalogue["cbc"].pk], "priority": "stat"}, format="json").status_code == 201


def test_emergency_surgery_defaults_to_urgent_workup(auth_client, req, catalogue):
    SurgeryRequest.objects.filter(pk=req.pk).update(priority="emergency")
    auth_client.post(f"/api/v1/ot/surgery-requests/{req.pk}/request-investigations/", {"lab_tests": [catalogue["cbc"].pk]}, format="json")
    assert LabOrder.objects.get(patient=req.patient).priority == "urgent"


def test_ot_records_show_who_updated_them(anaesthetist_client, req):
    r = anaesthetist_client.patch(f"/api/v1/ot/surgery-requests/{req.pk}/", {"priority": "urgent"}, format="json")
    assert r.status_code == 200, r.data
    assert r.data["updated_by_name"] == "Shweta Kapoor" and r.data["updated_at"]
    req.refresh_from_db()
    assert req.updated_by.email == "anaes@test-hospital.example"
