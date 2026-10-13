"""NABH diagnostics and patient-portal elements that had code but no test."""
from datetime import date

import pytest

from apps.clinical.models import ClinicalAlert
from apps.laboratory.models import LabOrder, LabTest
from apps.patients.models import Patient
from apps.portal.tests import _portal_client
from apps.radiology.models import RadiologyOrder, RadiologyProcedure


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Asha", last_name="Kale", mobile="9811100031", date_of_birth=date(1979, 4, 4))


@pytest.fixture
def glucose(hospital):
    return LabTest.objects.create(hospital=hospital, name="Blood glucose", code="RBS", unit="mg/dL", ref_low=70, ref_high=140)


@pytest.fixture
def xray(hospital):
    return RadiologyProcedure.objects.create(hospital=hospital, name="Chest X-Ray", modality=RadiologyProcedure.Modality.XRAY)


@pytest.fixture
def notified(monkeypatch):
    sent = []
    monkeypatch.setattr("apps.clinical.notify.notify_patient", lambda patient, purpose, context, **kw: sent.append((patient.pk, purpose, context)))
    return sent


def _verified_lab_order(auth_client, patient, test):
    order = auth_client.post("/api/v1/laboratory/orders/", {"patient": patient.pk, "ordered_tests": [test.pk]}, format="json").json()
    rid = auth_client.post("/api/v1/laboratory/results/", {"lab_order": order["id"], "lab_test": test.pk, "value": "101"}, format="json").json()["id"]
    assert auth_client.post(f"/api/v1/laboratory/results/{rid}/verify/").status_code == 200
    return LabOrder.objects.get(pk=order["id"])


def _verified_radiology_report(auth_client, patient, procedure):
    order = auth_client.post("/api/v1/radiology/orders/", {"patient": patient.pk, "procedure": procedure.pk}, format="json").json()
    rep = auth_client.post("/api/v1/radiology/reports/", {"radiology_order": order["id"], "findings": "Clear lung fields", "impression": "Normal"}, format="json").json()
    assert auth_client.post(f"/api/v1/radiology/reports/{rep['id']}/verify/").status_code == 200
    return RadiologyOrder.objects.get(pk=order["id"])


# NABH AAC.3.k
def test_lab_report_ready_notifies_patient_and_ordering_doctor(auth_client, patient, glucose, user, notified):
    order = _verified_lab_order(auth_client, patient, glucose)
    assert (patient.pk, "report_ready") == notified[-1][:2] and notified[-1][2]["report_type"] == "Laboratory"
    assert ClinicalAlert.objects.filter(target_user=user, title=f"Lab report ready: {order.order_number}").exists()
    assert order.patient_notified_at is not None


# NABH AAC.4.j
def test_radiology_report_ready_notifies_the_patient(auth_client, patient, xray, notified):
    order = _verified_radiology_report(auth_client, patient, xray)
    assert notified[-1][1] == "report_ready" and notified[-1][2] == {"report_type": "Radiology", "order_number": order.accession_number}
    assert order.patient_notified_at is not None


# NABH AAC.3.d
def test_lab_report_templates_created_and_edited(auth_client):
    tpl = auth_client.post("/api/v1/laboratory/report-templates/", {"name": "Biochemistry", "department": "biochemistry",
                                                                   "footer_text": "Method: GOD-POD"}, format="json")
    assert tpl.status_code == 201, tpl.json()
    edited = auth_client.patch(f"/api/v1/laboratory/report-templates/{tpl.json()['id']}/", {"signatory_designation": "Consultant Biochemist"}, format="json").json()
    assert edited["signatory_designation"] == "Consultant Biochemist" and edited["footer_text"] == "Method: GOD-POD"


# NABH AAC.4.d
def test_radiology_report_templates_per_modality(auth_client, xray):
    tpl = auth_client.post("/api/v1/radiology/templates/", {"name": "Normal chest", "modality": "xray", "procedure": xray.pk,
                                                           "findings": "Both lung fields clear. Cardiac silhouette normal.", "impression": "Normal study"}, format="json")
    assert tpl.status_code == 201, tpl.json()
    assert [t["name"] for t in auth_client.get("/api/v1/radiology/templates/?modality=xray").json()["results"]] == ["Normal chest"]
    assert auth_client.patch(f"/api/v1/radiology/templates/{tpl.json()['id']}/", {"impression": "No abnormality"}, format="json").json()["impression"] == "No abnormality"


# NABH AAC.3.m
def test_test_referred_to_an_external_lab_and_its_result_kept(auth_client, patient, glucose):
    order = auth_client.post("/api/v1/laboratory/orders/", {"patient": patient.pk, "ordered_tests": [glucose.pk]}, format="json").json()
    ref = auth_client.post("/api/v1/laboratory/outsourced-tests/", {"lab_order": order["id"], "lab_test": glucose.pk, "external_lab": "Metropolis"}, format="json")
    assert ref.status_code == 201, ref.json()
    assert auth_client.post(f"/api/v1/laboratory/outsourced-tests/{ref.json()['id']}/mark_sent/", {"external_reference": "MET-1"}, format="json").json()["status"] == "sent"
    done = auth_client.post(f"/api/v1/laboratory/outsourced-tests/{ref.json()['id']}/record_result/", {"result_text": "HbA1c 6.1%"}, format="json").json()
    assert done["status"] == "received" and done["result_text"] == "HbA1c 6.1%" and done["received_at"]


# NABH AAC.4.n
def test_outsourced_radiology_study_recorded(auth_client, patient, xray):
    res = auth_client.post("/api/v1/radiology/orders/", {"patient": patient.pk, "procedure": xray.pk, "is_outsourced": True, "outsourced_center": "City Imaging"}, format="json")
    assert res.status_code == 201, res.json()
    assert [o["outsourced_center"] for o in auth_client.get("/api/v1/radiology/orders/?is_outsourced=true").json()["results"]] == ["City Imaging"]


# NABH AAC.4.k
def test_patient_views_verified_radiology_report_in_portal(auth_client, hospital, patient, xray, monkeypatch, notified):
    _verified_radiology_report(auth_client, patient, xray)
    c, _ = _portal_client(hospital, patient.mobile, monkeypatch)
    reports = c.get(f"/api/v1/portal/reports/?patient={patient.pk}").json()
    assert [r["study"] for r in reports["radiology"]] == ["Chest X-Ray"] and reports["radiology"][0]["impression"] == "Normal"


# NABH AAC.7.a
def test_care_information_published_to_patients(auth_client, hospital, patient, monkeypatch):
    auth_client.post("/api/v1/portal/admin/care-information/", {"category": "rights", "title": "Your rights", "body": "You may ask for a second opinion."}, format="json")
    auth_client.post("/api/v1/portal/admin/care-information/", {"category": "general", "title": "Draft", "body": "x", "is_published": False}, format="json")
    c, _ = _portal_client(hospital, patient.mobile, monkeypatch)
    assert [r["title"] for r in c.get("/api/v1/portal/care-information/").json()] == ["Your rights"]


# NABH AAC.8.c
def test_patient_reported_outcome_measure(hospital, patient, monkeypatch):
    from apps.portal.models import PROM_PREM_INSTRUMENTS, PatientReportedMeasure

    c, _ = _portal_client(hospital, patient.mobile, monkeypatch)
    questions = PROM_PREM_INSTRUMENTS["prom_eq5d"]["questions"]
    res = c.post("/api/v1/portal/measures/", {"instrument": "prom_eq5d", "answers": {q["id"]: 2 for q in questions}}, format="json")
    assert res.status_code == 201, res.json()
    assert PatientReportedMeasure.objects.get(pk=res.json()["id"]).kind == "prom" and float(res.json()["score"]) == 2.0
