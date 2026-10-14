"""EMR & MRD parity with MedNet M2.22: Humpty Dumpty, intervention checklists, patient history and
header, signature + stamp on documents, MRD report pack, case type on the encounter."""
import datetime
import io
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.appointments.models import Appointment, Doctor, OPDVisit, Slot
from apps.clinical.models import Allergy, InterventionBundle, RiskAssessment
from apps.clinical.scoring import humpty_dumpty
from apps.facilities.models import Bed, Room, Ward
from apps.ipd.models import Admission
from apps.patients.models import Patient

C = "/api/v1/clinical"


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Anu", last_name="Patil", mobile="9811100401", gender="female", date_of_birth=datetime.date(2022, 5, 1))


@pytest.fixture
def admission(hospital, patient):
    ward = Ward.objects.create(hospital=hospital, name="Paeds", ward_type="general")
    bed = Bed.objects.create(hospital=hospital, room=Room.objects.create(hospital=hospital, ward=ward, room_number="P1"), bed_number="1")
    return Admission.objects.create(hospital=hospital, patient=patient, admitting_doctor=Doctor.objects.create(hospital=hospital, name="Rao"), bed=bed)


def png(w=200, h=80):
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), (0, 0, 120, 255)).save(buf, "PNG")
    return SimpleUploadedFile("img.png", buf.getvalue(), content_type="image/png")


HIGH_CHILD = {"age": "under_3", "gender": "female", "diagnosis": "neurological", "cognition": "not_aware_of_limitations",
              "environment": "assistive_device_or_crib", "surgery_sedation": "over_48h_or_none", "medication": "none"}


def test_humpty_dumpty_scoring():
    assert humpty_dumpty({}) == (7, "low")
    assert humpty_dumpty(HIGH_CHILD) == (4 + 1 + 4 + 3 + 3 + 1 + 1, "high")  # 17


def test_high_risk_gets_a_checklist_that_nurses_tick(auth_client, patient, admission):
    tools = {t["tool"]: t for t in auth_client.get(f"{C}/risk-assessments/tools/").data}
    assert {i["key"] for i in tools["humpty_dumpty"]["items"]} >= {"age", "diagnosis", "medication"}
    high = next(lv for lv in tools["humpty_dumpty"]["levels"] if lv["level"] == "high")
    assert high["high_risk"] and len(high["interventions"]) >= 5

    r = auth_client.post(f"{C}/risk-assessments/", {"patient": patient.pk, "admission": admission.pk, "tool": "humpty_dumpty", "answers": HIGH_CHILD}, format="json")
    assert r.status_code == 201, r.data
    rid = r.data["id"]
    obj = RiskAssessment.objects.get(pk=rid)
    assert obj.risk_level == "high" and obj.intervention_items == high["interventions"]
    first, second = obj.intervention_items[:2]
    res = auth_client.post(f"{C}/risk-assessments/{rid}/interventions/", {"done": [first, second, "Not on the list"]}, format="json").data
    assert set(res["interventions_done"]) == {first, second} and first not in res["interventions_pending"]
    res = auth_client.post(f"{C}/risk-assessments/{rid}/interventions/", {"undone": [second]}, format="json").data
    assert second in res["interventions_pending"]

    RiskAssessment.objects.filter(pk=rid).update(assessed_at=timezone.now() - timedelta(hours=6))
    pending = auth_client.get(f"{C}/risk-assessments/intervention-compliance/", {"hours": 4}).data
    assert [p["id"] for p in pending] == [rid] and second in pending[0]["pending"]


def test_hospital_checklist_replaces_the_default(auth_client, hospital, patient, admission):
    InterventionBundle.objects.create(hospital=hospital, tool="morse_fall", risk_level="high", items=["Our own fall bundle"])
    r = auth_client.post(f"{C}/risk-assessments/", {"patient": patient.pk, "admission": admission.pk, "tool": "morse_fall",
                                                     "answers": {"history_of_falling": True, "gait": "impaired", "secondary_diagnosis": True}}, format="json")
    assert RiskAssessment.objects.get(pk=r.data["id"]).intervention_items == ["Our own fall bundle"]


def test_history_and_header_cards(auth_client, hospital, patient, admission):
    Allergy.objects.create(hospital=hospital, patient=patient, allergen="Penicillin", severity="severe")
    bad = auth_client.put(f"{C}/patient-history/{patient.pk}/", {"surgical": [{"year": 2020}]}, format="json")
    assert bad.status_code == 400
    res = auth_client.put(f"{C}/patient-history/{patient.pk}/", {
        "past_medical": [{"condition": "Asthma", "since": "2023", "ongoing": True}, {"condition": "Jaundice", "ongoing": False}],
        "surgical": [{"procedure": "Hernia repair", "year": "2024"}], "family": [{"relation": "Mother", "condition": "Diabetes"}],
        "habits": {"smoking": "never", "passive_smoke": "yes"},
    }, format="json")
    assert res.status_code == 200 and res.data["updated_by_name"] is not None
    auth_client.post(f"{C}/risk-assessments/", {"patient": patient.pk, "admission": admission.pk, "tool": "humpty_dumpty", "answers": HIGH_CHILD}, format="json")
    h = auth_client.get(f"{C}/patient-header/{patient.pk}/").data
    assert [a["allergen"] for a in h["allergies"]] == ["Penicillin"]
    assert [c["condition"] for c in h["conditions"]] == ["Asthma"]  # resolved conditions aren't headlined
    assert h["surgeries"][0]["procedure"] == "Hernia repair" and h["habits"] == {"passive_smoke": "yes"}
    assert h["risks"][0]["level"] == "high" and h["patient"]["age"] >= 3


def test_signature_and_stamp_are_printed_at_the_chosen_size(auth_client, user, hospital, patient):
    assert auth_client.put("/api/v1/users/me/signature/", {"signature_width_mm": 200}, format="multipart").status_code == 400
    assert auth_client.put("/api/v1/users/me/signature/", {"stamp_image": SimpleUploadedFile("x.txt", b"hi", content_type="text/plain")}, format="multipart").status_code == 400
    res = auth_client.put("/api/v1/users/me/signature/", {"signature_image": png(), "stamp_image": png(100, 100), "stamp_width_mm": 24,
                                                         "registration_number": "MMC-12345"}, format="multipart").data
    assert res["signature_image"] and res["stamp_image"] and res["stamp_width_mm"] == 24
    user.refresh_from_db()

    from reportlab.platypus import Image as RLImage

    from apps.clinical.sign_block import signature_block

    block = signature_block(user, subtitle="Consultant")
    images = [c for row in block._cellvalues[0][0]._cellvalues for c in row if isinstance(c, RLImage)]
    assert len(images) == 2
    stamp = images[0]
    assert round(stamp.drawWidth / 2.8346, 0) == 24 and round(stamp.drawHeight, 1) == round(stamp.drawWidth, 1)  # square stamp keeps proportions

    from apps.patients.models import Prescription
    from apps.patients.prescription_pdf import render_prescription_pdf

    rx = Prescription.objects.create(hospital=hospital, patient=patient, doctor=user, medications=[{"name": "Paracetamol", "dose": "500 mg"}])
    assert render_prescription_pdf(rx).startswith(b"%PDF")
    res = auth_client.put("/api/v1/users/me/signature/", {"remove_stamp": "true"}, format="multipart").data
    assert res["stamp_image"] == ""


def test_mrd_report_pack(auth_client, hospital, patient, admission):
    from apps.emergency.models import EDVisit
    from apps.mrd.models import CodingRecord, ICD10Code

    now = timezone.now()
    Admission.objects.filter(pk=admission.pk).update(status="deceased", discharged_at=now)
    coded = Admission.objects.create(hospital=hospital, patient=patient, admitting_doctor=admission.admitting_doctor, bed=admission.bed, status="discharged")
    Admission.objects.filter(pk=coded.pk).update(discharged_at=now - timedelta(days=10))
    code = ICD10Code.objects.create(code="J45", title="Asthma")
    CodingRecord.objects.create(hospital=hospital, admission=coded, principal_diagnosis=code)  # coded, not verified
    EDVisit.objects.create(hospital=hospital, patient=patient, is_mlc=True, mlc_type="RTA", police_station="Kothrud")

    summary = auth_client.get("/api/v1/mrd/reports/").data
    assert (summary["uncoded"], summary["not_coded"], summary["overdue_coding"], summary["deaths"], summary["mlc"], summary["mlc_police_not_intimated"]) == (2, 1, 1, 1, 1, 1)
    uncoded = auth_client.get("/api/v1/mrd/reports/", {"kind": "uncoded"}).data["rows"]
    assert {r["state"] for r in uncoded} == {"Not coded", "Coded, not verified"}
    deaths = auth_client.get("/api/v1/mrd/reports/", {"kind": "deaths"}).data["rows"]
    assert deaths[0]["source"] == "IPD" and deaths[0]["cause_of_death"] == "Not coded"
    csv = auth_client.get("/api/v1/mrd/reports/", {"kind": "mlc", "output": "csv"}).content.decode()
    assert "Medico-legal cases" in csv and "Kothrud" in csv
    assert auth_client.get("/api/v1/mrd/reports/", {"kind": "nope"}).status_code == 400


def test_mrd_reports_need_mrd_access(restricted_user):
    c = APIClient()
    c.force_authenticate(user=restricted_user)
    assert c.get("/api/v1/mrd/reports/").status_code == 403


def test_encounter_case_type_can_be_changed(auth_client, hospital, patient):
    from apps.opd.models import Encounter

    doc = Doctor.objects.create(hospital=hospital, name="Mehta")
    slot = Slot.objects.create(hospital=hospital, doctor=doc, date=timezone.localdate(), start_time=datetime.time(9), end_time=datetime.time(9, 15))
    appt = Appointment.objects.create(hospital=hospital, patient=patient, doctor=doc, slot=slot)
    OPDVisit.objects.create(hospital=hospital, patient=patient, doctor=doc, appointment=appt, visit_type="new", payer_type="self")
    enc = Encounter.objects.get_or_create(hospital=hospital, appointment=appt, defaults={"patient": patient, "doctor": doc})[0]
    assert auth_client.get(f"/api/v1/opd/encounters/{enc.pk}/").data["case_type"] == "new_complaint"
    res = auth_client.post(f"/api/v1/opd/encounters/{enc.pk}/case-type/", {"case_type": "old_complaint"}, format="json")
    assert res.status_code == 200 and res.data["case_type"] == "old_complaint"
    assert auth_client.post(f"/api/v1/opd/encounters/{enc.pk}/case-type/", {"case_type": "x"}, format="json").status_code == 400
