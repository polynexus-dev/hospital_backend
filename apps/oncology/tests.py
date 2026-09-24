"""MRD, dietary, oncology and predictive-analytics tests."""
from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.appointments.models import Doctor
from apps.facilities.models import Bed, Room, Ward
from apps.patients.models import Patient


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Lata", mobile="9000000501", date_of_birth=date(1965, 6, 6))


@pytest.fixture
def admission(hospital, patient):
    from apps.ipd.services import admit_patient

    bed = Bed.objects.create(hospital=hospital, room=Room.objects.create(hospital=hospital, ward=Ward.objects.create(hospital=hospital, name="Onco ward"), room_number="1"), bed_number="O1")
    return admit_patient(hospital=hospital, patient=patient, admitting_doctor=Doctor.objects.create(hospital=hospital, name="Dr O"), bed=bed,
                         admission_type="planned", department=None, admission_diagnosis="Ca breast", source_encounter=None)


# --- MRD -------------------------------------------------------------------------


def test_icd10_search_and_discharge_creates_mrd_file(auth_client, hospital, admission):
    from apps.ipd.models import DischargeSummary
    from apps.ipd.services import discharge_patient
    from apps.mrd.models import MedicalRecordFile

    assert auth_client.get("/api/v1/mrd/icd10/?search=dengue").json()["results"][0]["code"].startswith("A9")
    DischargeSummary.objects.create(hospital=hospital, admission=admission)
    discharge_patient(admission)
    f = MedicalRecordFile.objects.get(admission=admission)
    assert f.file_number.startswith("MRD") and f.retention_until > timezone.localdate()


def test_mrd_issue_return_audit_and_coding(auth_client, hospital, admission):
    from apps.mrd.models import ICD10Code, MedicalRecordFile

    f = MedicalRecordFile.objects.create(hospital=hospital, patient=admission.patient, admission=admission)
    assert auth_client.post(f"/api/v1/mrd/files/{f.pk}/issue/", {"issued_to": "Dr X", "purpose": "Audit"}, format="json").status_code == 400  # not received yet
    auth_client.post(f"/api/v1/mrd/files/{f.pk}/receive/", {"rack_location": "R1-S3"}, format="json")
    assert auth_client.post(f"/api/v1/mrd/files/{f.pk}/issue/", {"issued_to": "Dr X", "purpose": "Audit", "due_back": str(timezone.localdate() - timedelta(days=1))}, format="json").json()["status"] == "issued"
    assert auth_client.get("/api/v1/mrd/files/overdue/").json()[0]["issued_to"] == "Dr X"
    assert auth_client.post(f"/api/v1/mrd/files/{f.pk}/return_file/").json()["status"] == "in_mrd"
    audit = auth_client.post(f"/api/v1/mrd/files/{f.pk}/audit/", {}, format="json").json()
    assert "discharge_summary" in audit["checklist"] and audit["score_pct"] < 100
    code = ICD10Code.objects.get(code="C50.9")
    assert auth_client.post("/api/v1/mrd/coding/", {"admission": admission.pk, "principal_diagnosis": code.pk}, format="json").status_code == 201
    assert auth_client.post(f"/api/v1/mrd/files/{f.pk}/destroy_record/").status_code == 400  # still in retention


# --- Dietary -------------------------------------------------------------------------


def test_diet_order_supersedes_and_kitchen_trays(auth_client, hospital, admission, patient):
    from apps.clinical.models import Allergy
    from apps.dietary.models import DietType

    Allergy.objects.create(hospital=hospital, patient=patient, allergen="peanut", allergen_type="food")
    dm = DietType.objects.get(hospital=hospital, code="DM")
    npo = DietType.objects.get(hospital=hospital, code="NPO")
    first = auth_client.post("/api/v1/dietary/orders/", {"patient": patient.pk, "admission": admission.pk, "diet_type": dm.pk}, format="json").json()
    assert first["food_allergies"] == "peanut"
    auth_client.post("/api/v1/dietary/menu/", {"diet_type": dm.pk, "meal": "lunch", "items": "Phulka, dal, salad"}, format="json")
    gen = auth_client.post("/api/v1/dietary/meals/generate/", {"meal": "lunch"}, format="json").json()
    assert gen["created"] == 1
    tray = auth_client.get("/api/v1/dietary/meals/?meal=lunch").json()["results"][0]
    assert tray["items"] == "Phulka, dal, salad" and tray["bed"] == "O1"
    assert auth_client.post(f"/api/v1/dietary/meals/{tray['id']}/deliver/", {"consumption_pct": 75}, format="json").json()["status"] == "delivered"
    second = auth_client.post("/api/v1/dietary/orders/", {"patient": patient.pk, "admission": admission.pk, "diet_type": npo.pk, "status": "npo"}, format="json").json()
    assert auth_client.get(f"/api/v1/dietary/orders/{first['id']}/").json()["status"] == "stopped"
    auth_client.post("/api/v1/dietary/meals/generate/", {"meal": "dinner"}, format="json")
    dinner = auth_client.get("/api/v1/dietary/meals/?meal=dinner").json()["results"][0]
    assert dinner["status"] == "held"
    assert auth_client.post(f"/api/v1/dietary/meals/{dinner['id']}/deliver/", format="json").status_code == 400
    assert second["id"]


# --- Oncology ------------------------------------------------------------------------


def test_chemo_bsa_dosing_double_check_and_counts(auth_client, hospital, patient, user):
    from rest_framework.test import APIClient

    from apps.accounts.models import User
    from apps.oncology.models import ChemoProtocol

    case = auth_client.post("/api/v1/oncology/cases/", {"patient": patient.pk, "primary_site": "Breast", "icdo3_topography": "C50.4", "t_stage": "2", "n_stage": "1", "m_stage": "0", "stage_group": "IIB"}, format="json").json()
    assert case["tnm"] == "T2N1M0"
    ac = ChemoProtocol.objects.get(hospital=hospital, name="AC (breast)")
    cyc = auth_client.post("/api/v1/oncology/chemo-cycles/", {"case": case["id"], "protocol": ac.pk, "cycle_number": 1, "indication": "adjuvant", "planned_on": str(timezone.localdate()),
                                                              "height_cm": 160, "weight_kg": 64, "pre_chemo_labs": {"ANC": 1.2, "platelets": 180}}, format="json").json()
    assert float(cyc["bsa"]) == 1.69 and cyc["calculated_doses"][0]["calculated_mg"] == 101.4
    assert auth_client.post(f"/api/v1/oncology/chemo-cycles/{cyc['id']}/verify/").status_code == 403  # prescriber can't self-verify
    checker = User.objects.create_user(email="pharm@x.example", password="x", hospital=hospital, role=user.role)
    from apps.accounts.models import assign_role

    assign_role(checker, user.role)
    c2 = APIClient()
    c2.force_authenticate(user=checker)
    assert c2.post(f"/api/v1/oncology/chemo-cycles/{cyc['id']}/verify/").status_code == 200
    assert auth_client.post(f"/api/v1/oncology/chemo-cycles/{cyc['id']}/administer/").json()["code"] == "counts_low"
    assert auth_client.post(f"/api/v1/oncology/chemo-cycles/{cyc['id']}/administer/", {"override": True}, format="json").json()["status"] == "given"
    death = auth_client.post(f"/api/v1/oncology/cases/{case['id']}/record_death/", {"date_of_death": str(timezone.localdate() + timedelta(days=10)), "cause_of_death": "Febrile neutropenia", "modality": "chemotherapy"}, format="json").json()
    assert death["death_within_30_days_of_treatment"] is True


def test_tumor_board_flow(auth_client, hospital, patient, user):
    from apps.clinical.models import ClinicalAlert

    case = auth_client.post("/api/v1/oncology/cases/", {"patient": patient.pk, "primary_site": "Lung"}, format="json").json()
    mt = auth_client.post("/api/v1/oncology/tumor-boards/", {"scheduled_at": (timezone.now() + timedelta(days=2)).isoformat(), "members": [user.pk]}, format="json").json()
    assert mt["board_id"].startswith("MDTB-")
    assert ClinicalAlert.objects.filter(target_user=user, title__startswith=mt["board_id"]).exists()
    auth_client.post(f"/api/v1/oncology/tumor-boards/{mt['id']}/add_case/", {"case": case["id"], "question": "Resectable?"}, format="json")
    auth_client.post(f"/api/v1/oncology/tumor-boards/{mt['id']}/record_attendance/", {"attendees": [{"member": user.pk, "specialty": "Surgical oncology", "designation": "Consultant"}]}, format="json")
    res = auth_client.post(f"/api/v1/oncology/tumor-boards/{mt['id']}/record_decision/", {"case": case["id"], "recommendation": "Lobectomy", "treatment_plan": "Surgery → adjuvant chemo"}, format="json").json()
    assert res["status"] == "held" and res["patients"][0]["recommendation"] == "Lobectomy" and res["attendance"][0]["specialty"] == "Surgical oncology"
    summary = auth_client.get(f"/api/v1/oncology/cases/{case['id']}/summary/").json()
    assert summary["board_reviews"][0]["treatment_plan"] == "Surgery → adjuvant chemo"


def test_trial_enrollment_requires_research_consent_and_randomises(auth_client, hospital, patient):
    from apps.clinical.models import ConsentRecord

    trial = auth_client.post("/api/v1/oncology/trials/", {"trial_id": "CTRI/2026/01/000123", "title": "Drug X vs standard", "arms": ["A", "B"], "ip_stock": 10}, format="json").json()
    wrong = ConsentRecord.objects.create(hospital=hospital, patient=patient, consent_type="general")
    assert auth_client.post("/api/v1/oncology/trial-enrollments/", {"trial": trial["id"], "patient": patient.pk, "consent": wrong.pk}, format="json").status_code == 400
    ok = ConsentRecord.objects.create(hospital=hospital, patient=patient, consent_type="research")
    enr = auth_client.post("/api/v1/oncology/trial-enrollments/", {"trial": trial["id"], "patient": patient.pk, "consent": ok.pk}, format="json").json()
    assert enr["subject_id"] == "CTRI/2026/01/000123-0001" and enr["arm"] in ("A", "B")
    assert auth_client.post(f"/api/v1/oncology/trial-enrollments/{enr['id']}/dispense_ip/", {"quantity": 11}, format="json").status_code == 409


def test_rt_fractions(auth_client, patient):
    case = auth_client.post("/api/v1/oncology/cases/", {"patient": patient.pk, "primary_site": "Cervix"}, format="json").json()
    plan = auth_client.post("/api/v1/oncology/radiotherapy/", {"case": case["id"], "site": "Pelvis", "technique": "IMRT", "intent": "curative", "total_dose_gy": 4, "fractions": 2, "immobilisation": "vac-lok"}, format="json").json()
    assert plan["dose_per_fraction"] == 2.0
    auth_client.post(f"/api/v1/oncology/radiotherapy/{plan['id']}/deliver_fraction/")
    done = auth_client.post(f"/api/v1/oncology/radiotherapy/{plan['id']}/deliver_fraction/").json()
    assert done["fractions_delivered"] == 2 and done["end_date"]
    assert auth_client.post(f"/api/v1/oncology/radiotherapy/{plan['id']}/deliver_fraction/").status_code == 400


# --- Predictive ------------------------------------------------------------------------


def test_predictive_endpoints(auth_client, hospital, admission):
    for url in ("predict/opd-footfall/", "predict/admissions/", "predict/beds/", "predict/staffing/", "predict/stockouts/", "predict/no-show/"):
        res = auth_client.get(f"/api/v1/{url}")
        assert res.status_code == 200, url
    staffing = auth_client.get("/api/v1/predict/staffing/").json()
    assert len(staffing["days"]) == 7 and staffing["days"][0]["nurses_needed_per_shift"] >= 1
    assert len(auth_client.get("/api/v1/predict/admissions/?days=5").json()["forecast"]) == 5
