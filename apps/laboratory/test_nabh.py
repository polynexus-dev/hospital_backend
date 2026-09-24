import struct
from datetime import date

import pytest

from apps.clinical.models import ClinicalAlert
from apps.laboratory.models import LabAnalyzer, LabOrder, LabResult, LabTest, SampleCollection
from apps.patients.models import Patient


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Meera", mobile="9000000201", date_of_birth=date(1980, 2, 2))


@pytest.fixture
def glucose(hospital):
    return LabTest.objects.create(hospital=hospital, name="Blood glucose (random)", code="RBS", loinc_code="2345-7", unit="mg/dL",
                                  ref_low=70, ref_high=140, critical_low=40, critical_high=400)


def _order(auth_client, patient, test, **extra):
    return auth_client.post("/api/v1/laboratory/orders/", {"patient": patient.pk, "ordered_tests": [test.pk], **extra}, format="json")


def test_order_number_and_duplicate_guard(auth_client, patient, glucose):
    first = _order(auth_client, patient, glucose)
    assert first.status_code == 201 and first.json()["order_number"].startswith("LAB")
    dup = _order(auth_client, patient, glucose)
    assert dup.status_code == 409 and dup.json()["code"] == "duplicate_order"
    assert _order(auth_client, patient, glucose, confirm_duplicate=True).status_code == 201


def test_specimen_number_tracking_and_rejection(auth_client, patient, glucose):
    order = _order(auth_client, patient, glucose).json()
    s = auth_client.post("/api/v1/laboratory/samples/", {"lab_order": order["id"], "sample_type": "Serum"}, format="json").json()
    assert s["barcode"].startswith("S")
    assert auth_client.get(f"/api/v1/laboratory/samples/track/?barcode={s['barcode']}").json()["uhid"] == patient.uhid
    assert auth_client.post(f"/api/v1/laboratory/samples/{s['id']}/reject/", {}, format="json").status_code == 400
    res = auth_client.post(f"/api/v1/laboratory/samples/{s['id']}/reject/", {"reason": "haemolysed"}, format="json").json()
    assert res["status"] == "rejected"
    assert LabOrder.objects.get(pk=order["id"]).status == "ordered"
    label = auth_client.get(f"/api/v1/laboratory/samples/{s['id']}/label/")
    assert label.status_code == 200 and label.content[:4] == b"%PDF"


def test_auto_flag_and_critical_alert_to_orderer(auth_client, patient, glucose, user):
    order = _order(auth_client, patient, glucose).json()
    r = auth_client.post("/api/v1/laboratory/results/", {"lab_order": order["id"], "lab_test": glucose.pk, "value": "450", "flag": "normal"}, format="json")
    assert r.status_code == 201
    assert LabResult.objects.get(pk=r.json()["id"]).flag == "critical"
    assert ClinicalAlert.objects.filter(patient=patient, alert_type="critical_result", target_user=user).exists()
    r2 = auth_client.post("/api/v1/laboratory/results/", {"lab_order": order["id"], "lab_test": glucose.pk, "value": "150"}, format="json")
    assert LabResult.objects.get(pk=r2.json()["id"]).flag == "high"


def test_signed_result_amend_is_logged_and_report_marks_provisional(auth_client, patient, glucose):
    order = _order(auth_client, patient, glucose).json()
    rid = auth_client.post("/api/v1/laboratory/results/", {"lab_order": order["id"], "lab_test": glucose.pk, "value": "95"}, format="json").json()["id"]
    provisional = auth_client.get(f"/api/v1/laboratory/orders/{order['id']}/report/")
    assert provisional.status_code == 200 and provisional.content[:4] == b"%PDF"
    assert auth_client.post(f"/api/v1/laboratory/results/{rid}/verify/").status_code == 200
    assert auth_client.patch(f"/api/v1/laboratory/results/{rid}/", {"value": "99"}, format="json").status_code >= 400
    res = auth_client.post(f"/api/v1/laboratory/results/{rid}/amend/", {"value": "59", "reason": "Transcription error"}, format="json").json()
    assert res["is_amended"] is True and res["flag"] == "low"
    from apps.core.models import Amendment

    assert Amendment.objects.filter(object_id=str(rid), previous_value="95", corrected_value="59").exists()


def test_hl7_analyzer_import(api_client, hospital, patient, glucose):
    order = LabOrder.objects.create(hospital=hospital, patient=patient)
    order.ordered_tests.add(glucose)
    SampleCollection.objects.create(hospital=hospital, lab_order=order, sample_type="Serum", barcode="S2609230001")
    LabAnalyzer.objects.create(hospital=hospital, name="Cobas c311", api_token="tok123", test_code_map={"GLU": "RBS"})
    msg = "MSH|^~\\&|COBAS|LAB|HIS|HOSP|202609231000||ORU^R01|1|P|2.5\rPID|1||UHID1\rOBR|1||S2609230001|RBS\rOBX|1|NM|GLU^Glucose||38|mg/dL|70-140|LL"
    res = api_client.post("/api/v1/laboratory/analyzer/results/", {"message": msg}, format="json", HTTP_X_ANALYZER_TOKEN="tok123")
    assert res.status_code == 200 and res.json()["results_saved"] == ["Blood glucose (random)"]
    assert LabResult.objects.get(lab_order=order).flag == "critical"
    assert api_client.post("/api/v1/laboratory/analyzer/results/", {"message": msg}, format="json").status_code == 401


# --- Radiology ---------------------------------------------------------------


def _dicom_bytes(study_uid):
    def el(g, e, vr, val):
        val = val + (b"\x00" if len(val) % 2 else b"")
        return struct.pack("<HH", g, e) + vr + struct.pack("<H", len(val)) + val
    return b"\x00" * 128 + b"DICM" + el(0x0008, 0x0060, b"CS", b"CT") + el(0x0020, 0x000D, b"UI", study_uid.encode())


def test_dicom_header_reader():
    from apps.radiology.dicom import read_tags

    tags = read_tags(_dicom_bytes("1.2.840.113619.2.55.3"))
    assert tags["study_instance_uid"] == "1.2.840.113619.2.55.3" and tags["modality"] == "CT"


def test_radiology_contraindication_blocks_until_override(auth_client, hospital, patient):
    from apps.clinical.models import ClinicalAssessment
    from apps.radiology.models import RadiologyProcedure

    ClinicalAssessment.objects.create(hospital=hospital, patient=patient, provisional_diagnosis="CKD stage 4")
    ct = RadiologyProcedure.objects.create(hospital=hospital, name="CECT abdomen", modality="ct", uses_contrast=True)
    blocked = auth_client.post("/api/v1/radiology/orders/", {"patient": patient.pk, "procedure": ct.pk}, format="json")
    assert blocked.status_code == 409 and blocked.json()["code"] == "contraindicated"
    ok = auth_client.post("/api/v1/radiology/orders/", {"patient": patient.pk, "procedure": ct.pk, "contraindication_override_reason": "eGFR 45 — benefit outweighs risk, hydrated"}, format="json")
    assert ok.status_code == 201 and ok.json()["accession_number"].startswith("RAD")
    assert ClinicalAlert.objects.filter(target_department="radiology").exists()


def test_radiology_slot_booking_status_and_amendment(auth_client, hospital, patient):
    from django.utils import timezone

    from apps.radiology.models import RadiologyEquipment, RadiologyProcedure, RadiologyReport

    xr = RadiologyProcedure.objects.create(hospital=hospital, name="Chest X-ray PA", modality="xray", duration_minutes=10)
    eq = RadiologyEquipment.objects.create(hospital=hospital, name="DR Room 1", modality="xray")
    o1 = auth_client.post("/api/v1/radiology/orders/", {"patient": patient.pk, "procedure": xr.pk}, format="json").json()
    o2 = auth_client.post("/api/v1/radiology/orders/", {"patient": patient.pk, "procedure": xr.pk, "confirm_duplicate": True}, format="json").json()
    start = (timezone.now() + timezone.timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0).isoformat()
    assert auth_client.post(f"/api/v1/radiology/orders/{o1['id']}/book_slot/", {"equipment": eq.pk, "start": start}, format="json").json()["status"] == "scheduled"
    assert auth_client.post(f"/api/v1/radiology/orders/{o2['id']}/book_slot/", {"equipment": eq.pk, "start": start}, format="json").status_code == 409
    for step in ("arrive", "start", "complete"):
        assert auth_client.post(f"/api/v1/radiology/orders/{o1['id']}/status/{step}/").status_code == 200
    rep = auth_client.post("/api/v1/radiology/reports/", {"radiology_order": o1["id"], "findings": "Clear lungs", "impression": "Normal"}, format="json").json()
    auth_client.post(f"/api/v1/radiology/reports/{rep['id']}/verify/")
    res = auth_client.post(f"/api/v1/radiology/reports/{rep['id']}/amend/", {"impression": "Small left effusion", "reason": "Missed on first read"}, format="json").json()
    assert res["is_amended"] is True and RadiologyReport.objects.get(pk=rep["id"]).impression == "Small left effusion"
    assert len(auth_client.get(f"/api/v1/radiology/reports/{rep['id']}/amendments/").json()) == 1
    hist = auth_client.get(f"/api/v1/radiology/orders/{o1['id']}/").json()["status_history"]
    assert [h["status"] for h in hist][:4] == ["ordered", "scheduled", "arrived", "in_progress"]
