"""End-to-end tests for the NABH clinical-module extensions (OT, blood
bank, ICU, IPD discharge, ED/MLC, support services) and the quality app
(incidents, medication errors, emergency codes, KPIs)."""
from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.appointments.models import Doctor
from apps.facilities.models import Bed, Room, Ward
from apps.ipd.models import Admission, DischargeSummary
from apps.patients.models import Patient


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Ravi", last_name="K", mobile="9000000101", date_of_birth=date(1970, 1, 1), blood_group="o_positive")


@pytest.fixture
def doctor(hospital):
    return Doctor.objects.create(hospital=hospital, name="Dr Rao")


@pytest.fixture
def bed(hospital):
    ward = Ward.objects.create(hospital=hospital, name="Ward A")
    room = Room.objects.create(hospital=hospital, ward=ward, room_number="101")
    return Bed.objects.create(hospital=hospital, room=room, bed_number="A1")


@pytest.fixture
def admission(hospital, patient, doctor, bed):
    from apps.ipd.services import admit_patient

    return admit_patient(hospital=hospital, patient=patient, admitting_doctor=doctor, bed=bed, admission_type="planned", department=None, admission_diagnosis="Cholelithiasis", source_encounter=None)


# --- OT (COP.6) ---------------------------------------------------------------


def test_ot_checklist_order_and_start_requires_timeout(auth_client, hospital, patient, doctor, admission):
    from apps.ot.models import OTSchedule, SurgeryRequest, SurgicalSafetyChecklist

    sr = SurgeryRequest.objects.create(hospital=hospital, patient=patient, admission=admission, proposed_procedure="Lap chole")
    start = timezone.now() + timedelta(hours=1)
    sch = OTSchedule.objects.create(hospital=hospital, surgery_request=sr, operation_theatre_room="OT1", surgeon=doctor, scheduled_start=start, scheduled_end=start + timedelta(hours=2))
    assert auth_client.post(f"/api/v1/ot/schedules/{sch.pk}/start/").status_code == 400

    res = auth_client.post("/api/v1/ot/safety-checklists/", {"ot_schedule": sch.pk, "patient": patient.pk, "procedure_name": "Lap chole"}, format="json")
    cid = res.json()["id"]
    all_in = {k: True for k in SurgicalSafetyChecklist.SIGN_IN_ITEMS}
    all_to = {k: True for k in SurgicalSafetyChecklist.TIME_OUT_ITEMS}
    assert auth_client.post(f"/api/v1/ot/safety-checklists/{cid}/phase/time_out/", {"answers": all_to}, format="json").status_code == 400  # out of order
    assert auth_client.post(f"/api/v1/ot/safety-checklists/{cid}/phase/sign_in/", {"answers": {"identity_confirmed": True}}, format="json").status_code == 400  # incomplete
    assert auth_client.post(f"/api/v1/ot/safety-checklists/{cid}/phase/sign_in/", {"answers": all_in}, format="json").status_code == 200
    given = (timezone.now() - timedelta(minutes=30)).isoformat()
    assert auth_client.post(f"/api/v1/ot/safety-checklists/{cid}/phase/time_out/", {"answers": all_to, "antibiotic_given_at": given}, format="json").status_code == 200
    assert auth_client.post(f"/api/v1/ot/schedules/{sch.pk}/start/").status_code == 200
    assert SurgicalSafetyChecklist.objects.get(pk=cid).antibiotic_given_within_60_min is True
    assert auth_client.post(f"/api/v1/ot/schedules/{sch.pk}/end/").json()["status"] == "completed"


def test_ot_reschedule_and_cancel_need_reasons(auth_client, hospital, patient, doctor):
    from apps.ot.models import OTSchedule, SurgeryRequest

    sr = SurgeryRequest.objects.create(hospital=hospital, patient=patient, proposed_procedure="Hernia repair")
    s = timezone.now() + timedelta(days=1)
    sch = OTSchedule.objects.create(hospital=hospital, surgery_request=sr, operation_theatre_room="OT2", surgeon=doctor, scheduled_start=s, scheduled_end=s + timedelta(hours=1))
    assert auth_client.post(f"/api/v1/ot/schedules/{sch.pk}/reschedule/", {}, format="json").status_code == 400
    res = auth_client.post(f"/api/v1/ot/schedules/{sch.pk}/reschedule/", {"scheduled_start": (s + timedelta(days=1)).isoformat(), "scheduled_end": (s + timedelta(days=1, hours=1)).isoformat(), "reason": "Surgeon unavailable"}, format="json")
    assert res.json()["reschedule_count"] == 1
    assert auth_client.post(f"/api/v1/ot/schedules/{sch.pk}/cancel/", {"reason": "Patient unfit"}, format="json").json()["status"] == "cancelled"


# --- Blood bank (COP.3) ---------------------------------------------------------


def test_blood_tat_steps_and_reaction_opens_incident(auth_client, hospital, patient):
    from apps.bloodbank.models import BloodUnit, CrossMatchRequest
    from apps.quality.models import SafetyIncident

    unit = BloodUnit.objects.create(hospital=hospital, blood_group="O+", component="prbc", collection_date=timezone.localdate(), expiry_date=timezone.localdate() + timedelta(days=30))
    req = auth_client.post("/api/v1/bloodbank/cross-matches/", {"patient": patient.pk, "blood_group_required": "O+", "component": "prbc", "urgency": "emergency"}, format="json").json()
    for step in ("sample_received", "grouping"):
        assert auth_client.post(f"/api/v1/bloodbank/cross-matches/{req['id']}/step/{step}/", format="json").status_code == 200
    assert auth_client.post(f"/api/v1/bloodbank/cross-matches/{req['id']}/step/crossmatch/", {"unit": unit.pk}, format="json").json()["reserved_unit"] == unit.pk
    CrossMatchRequest.objects.filter(pk=req["id"]).update(created_at=timezone.now() - timedelta(hours=2))
    assert auth_client.post(f"/api/v1/bloodbank/cross-matches/{req['id']}/step/issue/", format="json").status_code == 400  # late → reason needed
    assert auth_client.post(f"/api/v1/bloodbank/cross-matches/{req['id']}/step/issue/", {"delay_reason": "Antibody screen positive"}, format="json").status_code == 200

    t = auth_client.post("/api/v1/bloodbank/transfusions/", {"blood_unit": unit.pk, "patient": patient.pk}, format="json")
    assert t.status_code == 201, t.json()
    auth_client.post(f"/api/v1/bloodbank/transfusions/{t.json()['id']}/report_reaction/", {"reaction_type": "febrile", "severity": "mild"}, format="json")
    assert SafetyIncident.objects.filter(patient=patient, incident_type="transfusion_reaction").exists()


def test_transfusion_rejects_incompatible_unit_and_self_verification(auth_client, hospital, patient, user):
    from apps.bloodbank.models import BloodUnit

    a_pos = BloodUnit.objects.create(hospital=hospital, blood_group="A+", component="prbc", collection_date=timezone.localdate(), expiry_date=timezone.localdate() + timedelta(days=30))
    assert auth_client.post("/api/v1/bloodbank/transfusions/", {"blood_unit": a_pos.pk, "patient": patient.pk}, format="json").status_code == 400
    o_neg = BloodUnit.objects.create(hospital=hospital, blood_group="O-", component="prbc", collection_date=timezone.localdate(), expiry_date=timezone.localdate() + timedelta(days=30))
    assert auth_client.post("/api/v1/bloodbank/transfusions/", {"blood_unit": o_neg.pk, "patient": patient.pk, "bedside_verified_by": user.pk}, format="json").status_code == 400


def test_public_blood_stock(api_client, hospital):
    from apps.bloodbank.models import BloodUnit

    BloodUnit.objects.create(hospital=hospital, blood_group="B+", component="ffp", collection_date=timezone.localdate(), expiry_date=timezone.localdate() + timedelta(days=300))
    res = api_client.get("/api/v1/bloodbank/public/stock/?subdomain=test-hospital")
    assert res.status_code == 200 and res.json()["stock"][0]["units"] == 1


# --- ICU (COP.5) ------------------------------------------------------------


def test_apache_and_sofa_scoring():
    from apps.icu.scoring import apache_ii, sofa

    score, mortality = apache_ii({"temperature_c": 39.5, "mean_arterial_pressure": 60, "heart_rate": 145, "respiratory_rate": 36, "pao2": 58, "arterial_ph": 7.2, "sodium": 132, "potassium": 3.2, "creatinine_mg_dl": 2.4, "hematocrit": 28, "wbc_thousands": 22, "gcs": 10, "age": 70})
    assert score == 3 + 2 + 3 + 3 + 3 + 3 + 0 + 1 + 3 + 2 + 2 + 5 + 5
    assert 70 < mortality < 95
    assert sofa({"pao2_fio2": 180, "respiratory_support": True, "platelets_thousands": 90, "bilirubin_mg_dl": 2.5, "vasopressor": "dopamine_mid_or_norepi_low", "gcs": 12, "creatinine_mg_dl": 2.1})[0] == 3 + 2 + 2 + 3 + 2 + 2


def test_icu_admission_scores_and_discharge_needs_criteria(auth_client, hospital, admission, bed):
    from apps.icu.models import ICUAdmission

    icu = ICUAdmission.objects.create(hospital=hospital, admission=admission, bed=bed, admission_criteria_met=["SHOCK"], severity_scale="sofa", severity_inputs={"gcs": 8, "mean_arterial_pressure": 60})
    assert icu.is_eligible is True and icu.severity_score == 4 and icu.predicted_mortality is not None
    assert auth_client.post(f"/api/v1/icu/admissions/{icu.pk}/discharge/", {"outcome": "ward"}, format="json").status_code == 400
    assert auth_client.post(f"/api/v1/icu/admissions/{icu.pk}/discharge/", {"outcome": "ward", "discharge_criteria_met": ["STABLE_HAEMO"]}, format="json").status_code == 200


# --- IPD (AAC.5/6) -----------------------------------------------------------


def test_admission_notifies_departments_and_discharge_requires_clearances(auth_client, hospital, patient, doctor, bed):
    from apps.clinical.models import ClinicalAlert
    from apps.support_services.models import HousekeepingTask

    res = auth_client.post("/api/v1/ipd/admissions/", {"patient": patient.pk, "admitting_doctor": doctor.pk, "bed": bed.pk, "admission_type": "planned", "admission_diagnosis": "Pneumonia"}, format="json")
    assert res.status_code == 201, res.json()
    adm_id = res.json()["id"]
    assert ClinicalAlert.objects.filter(object_id=str(adm_id), target_department="dietary").exists()

    clearances = auth_client.post(f"/api/v1/ipd/admissions/{adm_id}/initiate_discharge/", {"departments": ["billing", "pharmacy"]}, format="json").json()
    DischargeSummary.objects.create(hospital=hospital, admission_id=adm_id, final_diagnosis="Pneumonia")
    assert auth_client.post(f"/api/v1/ipd/admissions/{adm_id}/discharge/", {"status": "discharged"}, format="json").status_code == 400
    for c in clearances:
        auth_client.post(f"/api/v1/ipd/discharge-clearances/{c['id']}/clear/", format="json")
    assert auth_client.post(f"/api/v1/ipd/admissions/{adm_id}/discharge/", {"status": "discharged"}, format="json").status_code == 200
    assert HousekeepingTask.objects.filter(bed=bed, task_type="terminal").exists()


def test_bed_board_and_prediction(auth_client, admission):
    Admission.objects.filter(pk=admission.pk).update(expected_discharge_date=timezone.localdate())
    res = auth_client.get("/api/v1/ipd/bed-board/")
    body = res.json()
    assert body["summary"]["occupied"] == 1
    assert body["prediction"]["expected_free"]["24h"] == 1


# --- ED MLC (COP.4.b) ---------------------------------------------------------


def test_mark_mlc(auth_client, hospital, patient):
    from apps.emergency.models import EDVisit

    v = EDVisit.objects.create(hospital=hospital, patient=patient, chief_complaint="RTA")
    assert auth_client.post(f"/api/v1/emergency/ed-visits/{v.pk}/mark_mlc/", {"mlc_type": "RTA"}, format="json").status_code == 400
    res = auth_client.post(f"/api/v1/emergency/ed-visits/{v.pk}/mark_mlc/", {"mlc_type": "RTA", "police_station": "Sitabuldi PS", "checklist": {"police_intimation_sent": True}}, format="json")
    assert res.status_code == 200 and res.json()["mlc_number"].startswith("MLC/")


# --- Support services ---------------------------------------------------------


def test_ambulance_trip_device_feed_and_arrival_creates_ed_visit(auth_client, api_client, hospital, patient):
    from apps.emergency.models import EDVisit

    amb = auth_client.post("/api/v1/support-services/ambulances/", {"vehicle_number": "MH31-AB-1234", "kind": "als"}, format="json").json()
    token = auth_client.post(f"/api/v1/support-services/ambulances/{amb['id']}/rotate_device_token/").json()["device_token"]
    trip = auth_client.post("/api/v1/support-services/ambulance-trips/", {"ambulance": amb["id"], "patient": patient.pk, "pickup_address": "Sadar", "chief_complaint": "Chest pain"}, format="json").json()
    for step in ("dispatch", "at_scene", "depart_scene"):
        assert auth_client.post(f"/api/v1/support-services/ambulance-trips/{trip['id']}/transition/{step}/").status_code == 200
    res = api_client.post("/api/v1/support-services/ambulance-device/feed/", {"heart_rate": 118, "spo2": 91, "latitude": 21.14, "longitude": 79.08}, format="json", HTTP_X_DEVICE_TOKEN=token)
    assert res.status_code == 200
    incoming = auth_client.get("/api/v1/support-services/ambulance-trips/incoming/").json()
    assert incoming[0]["latest_vitals"]["heart_rate"] == 118
    auth_client.post(f"/api/v1/support-services/ambulance-trips/{trip['id']}/transition/arrive/")
    assert EDVisit.objects.filter(patient=patient, ambulance_trip_id=trip["id"]).exists()


def test_cssd_failed_cycle_recalls_packs(auth_client, hospital):
    iset = auth_client.post("/api/v1/support-services/instrument-sets/", {"name": "Lap set", "code": "LAP1", "items": [{"name": "trocar", "count": 3}]}, format="json").json()
    cycle = auth_client.post("/api/v1/support-services/sterilization-cycles/", {"cycle_number": "C-100", "sterilizer": "Autoclave 1"}, format="json").json()
    pack = auth_client.post("/api/v1/support-services/sterile-batches/", {"instrument_set": iset["id"], "cycle": cycle["id"]}, format="json").json()
    assert auth_client.post(f"/api/v1/support-services/sterile-batches/{pack['id']}/issue/", format="json").status_code == 400  # indicators pending
    auth_client.patch(f"/api/v1/support-services/sterilization-cycles/{cycle['id']}/", {"biological_indicator_passed": False, "chemical_indicator_passed": True}, format="json")
    assert auth_client.get(f"/api/v1/support-services/sterile-batches/{pack['id']}/").json()["status"] == "recalled"


def test_equipment_due_list_and_breakdown(auth_client):
    asset = auth_client.post("/api/v1/support-services/equipment/", {"asset_tag": "VENT-01", "name": "Ventilator", "purchase_date": str(timezone.localdate() - timedelta(days=200)), "pm_frequency_days": 180, "is_critical": True}, format="json").json()
    due = auth_client.get("/api/v1/support-services/equipment/due/").json()
    assert any(d["asset_tag"] == "VENT-01" and d["due"] == "pm" and d["overdue"] for d in due)
    rec = auth_client.post("/api/v1/support-services/maintenance/", {"asset": asset["id"], "kind": "breakdown", "problem": "Alarm fault"}, format="json").json()
    assert auth_client.get(f"/api/v1/support-services/equipment/{asset['id']}/").json()["status"] == "breakdown"
    auth_client.post(f"/api/v1/support-services/maintenance/{rec['id']}/close/", {"work_done": "Sensor replaced"}, format="json")
    assert auth_client.get(f"/api/v1/support-services/equipment/{asset['id']}/").json()["status"] == "in_use"


# --- Quality (COP.8.c, COP.4.d, MOM.4, IMS.2) ------------------------------


def test_sentinel_incident_cannot_close_without_rca(auth_client, patient):
    res = auth_client.post("/api/v1/quality/incidents/", {"incident_type": "wrong_site", "description": "Wrong side marked", "patient": patient.pk}, format="json")
    assert res.json()["is_sentinel"] is True
    assert auth_client.post(f"/api/v1/quality/incidents/{res.json()['id']}/close/").status_code == 400


def test_medication_error_opens_incident_and_dashboard(auth_client, patient):
    res = auth_client.post("/api/v1/quality/medication-errors/", {"patient": patient.pk, "medication": "Insulin", "stage": "administration", "error_type": "wrong_dose", "category": "E"}, format="json")
    assert res.status_code == 201 and res.json()["incident"] is not None
    dash = auth_client.get("/api/v1/quality/medication-errors/dashboard/").json()
    assert dash["total"] == 1 and dash["harmful"] == 1 and dash["by_stage"]["administration"] == 1


def test_code_blue_alerts_responders_and_records_response(auth_client, hospital, user):
    from apps.clinical.models import ClinicalAlert
    from apps.quality.models import EmergencyCode

    code = EmergencyCode.objects.get(hospital=hospital, code="Code Blue")
    code.responders.add(user)
    act = auth_client.post("/api/v1/quality/code-activations/", {"code": code.pk, "location": "Ward A bed 3"}, format="json").json()
    assert ClinicalAlert.objects.filter(target_user=user, title__contains="Code Blue").exists()
    res = auth_client.post(f"/api/v1/quality/code-activations/{act['id']}/respond/", {"role": "Team leader"}, format="json").json()
    assert res["responses"][0]["role"] == "Team leader" and res["first_response_minutes"] is not None


def test_kpis_compute_and_export_all_formats(auth_client, hospital, admission, patient):
    from apps.quality.models import SafetyIncident

    SafetyIncident.objects.create(hospital=hospital, incident_type="fall", harm="mild", patient=patient, description="Fell in bathroom")
    SafetyIncident.objects.create(hospital=hospital, incident_type="other", harm="near_miss", description="Near miss")
    Admission.objects.filter(pk=admission.pk).update(admitted_at=timezone.now() - timedelta(days=10))
    res = auth_client.get("/api/v1/quality/kpis/?kind=nabh")
    rows = {r["code"]: r for r in res.json()["kpis"]}
    assert rows["K28"]["source"] == "system" and rows["K28"]["value"] > 0
    assert rows["K29"]["value"] == 50.0
    assert rows["K03"]["source"] == "unavailable"
    for fmt, marker in (("json", b'"kpis"'), ("csv", b"code,name"), ("xml", b"<NABHKPIReport"), ("xlsx", b"PK"), ("pdf", b"%PDF")):
        r = auth_client.get(f"/api/v1/quality/kpis/?export={fmt}")
        assert r.status_code == 200 and marker in r.content[:400], fmt


def test_manual_kpi_entry_overrides_and_quarter_publish(auth_client, hospital):
    from apps.quality.kpis import previous_quarter
    from apps.quality.models import KPISnapshot

    start, end = previous_quarter()
    auth_client.post("/api/v1/quality/kpi-manual-entries/", {"kpi_code": "K03", "period_start": str(start), "period_end": str(end), "numerator": 45, "denominator": 50}, format="json")
    res = auth_client.get(f"/api/v1/quality/kpis/?start={start}&end={end}").json()
    k03 = next(r for r in res["kpis"] if r["code"] == "K03")
    assert k03["source"] == "manual" and k03["value"] == 90.0
    assert auth_client.post("/api/v1/quality/kpis/publish/", {}, format="json").json()["published"] > 30
    assert KPISnapshot.objects.filter(hospital=hospital, kpi_code="K03", is_published=True).exists()


# --- Infection control (COP.8) --------------------------------------------


def test_cauti_rate_uses_catheter_days(auth_client, hospital, patient, admission):
    from apps.infection_control.models import DeviceEpisode, HAIIncident

    DeviceEpisode.objects.create(hospital=hospital, patient=patient, admission=admission, device="urinary_catheter", inserted_at=timezone.now() - timedelta(days=10), removed_at=timezone.now())
    HAIIncident.objects.create(hospital=hospital, patient=patient, admission=admission, infection_type="cauti", status="confirmed")
    rates = auth_client.get("/api/v1/infection-control/hai/rates/").json()["rates"]
    cauti = next(r for r in rates if r["code"] == "K12")
    assert cauti["denominator"] == 10 and cauti["value"] == 100.0


def test_staff_exposure_followups_and_antimicrobial_self_approval_blocked(auth_client, user, patient):
    res = auth_client.post("/api/v1/infection-control/staff-exposures/", {"staff": user.pk, "exposure_type": "needlestick", "source_patient": patient.pk}, format="json").json()
    assert [f["label"] for f in res["followups"]] == ["Baseline", "6 weeks", "3 months", "6 months"]
    appr = auth_client.post("/api/v1/infection-control/antimicrobial-approvals/", {"patient": patient.pk, "drug": "Meropenem", "indication": "Sepsis"}, format="json").json()
    assert auth_client.post(f"/api/v1/infection-control/antimicrobial-approvals/{appr['id']}/approve/").status_code == 403
