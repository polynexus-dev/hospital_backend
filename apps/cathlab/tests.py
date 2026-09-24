import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.appointments.models import Doctor
from apps.billing.models import Bill
from apps.cathlab.models import CathProcedure
from apps.emergency.models import EDVisit
from apps.facilities.models import Bed, Room, Ward
from apps.finance.models import ServiceTariff
from apps.ipd.services import admit_patient
from apps.patients.models import Patient


@pytest.fixture
def s(hospital, department):
    doctor = Doctor.objects.create(hospital=hospital, department=department, name="Kapoor", speciality="Cardiology")
    patient = Patient.objects.create(hospital=hospital, first_name="Suresh", mobile="9876500051")
    ward = Ward.objects.create(hospital=hospital, name="CCU", ward_type="icu")
    bed = Bed.objects.create(hospital=hospital, room=Room.objects.create(hospital=hospital, ward=ward, room_number="1"), bed_number="1", bed_type="icu")
    adm = admit_patient(hospital=hospital, patient=patient, admitting_doctor=doctor, bed=bed)
    tariff = ServiceTariff.objects.create(hospital=hospital, code="PPCI", name="Primary PCI", rate=90000, department="Cardiology")
    return {"doctor": doctor, "patient": patient, "adm": adm, "tariff": tariff, "hospital": hospital}


def create(client, s, **extra):
    body = {"patient": s["patient"].pk, "admission": s["adm"].pk, "procedure_type": "primary_pci", "urgency": "emergency",
            "indication": "Anterior STEMI", "operator": s["doctor"].pk, "procedure_tariff": s["tariff"].pk, **extra}
    res = client.post("/api/v1/cathlab/procedures/", body, format="json")
    assert res.status_code == 201, res.data
    return res.data["id"]


@pytest.mark.django_db
def test_primary_pci_flow_door_to_device_and_finalise_rules(auth_client, s):
    ed = EDVisit.objects.create(hospital=s["hospital"], patient=s["patient"], chief_complaint="Chest pain")
    EDVisit.objects.filter(pk=ed.pk).update(arrived_at=timezone.now() - datetime.timedelta(minutes=120))
    pid = create(auth_client, s, ed_visit=ed.pk)
    base = f"/api/v1/cathlab/procedures/{pid}/"
    assert auth_client.post(base + "reperfusion/").status_code == 400  # not started
    started = auth_client.post(base + "start/")
    assert started.status_code == 200 and started.data["door_at"] is not None  # taken from ED arrival
    rep = auth_client.post(base + "reperfusion/")
    assert rep.data["door_to_device_minutes"] == 120
    assert any("Door-to-device 120 min" in f for f in rep.data["safety_flags"])
    auth_client.post(base + "complete/")

    assert auth_client.post(base + "finalize/").status_code == 400  # no conclusion
    auth_client.patch(base, {"conclusion": "LAD culprit, DES x1, TIMI 3 achieved", "contrast_ml": 180, "fluoro_minutes": "14.5"}, format="json")
    res = auth_client.post(base + "finalize/")
    assert res.status_code == 400 and "delay_reason" in res.data
    auth_client.patch(base, {"delay_reason": "Cath team called in after hours"}, format="json")
    assert auth_client.post(base + "finalize/").status_code == 200
    assert auth_client.patch(base, {"conclusion": "edited"}, format="json").status_code == 400  # locked


@pytest.mark.django_db
def test_findings_validation_and_contrast_radiation_flags(auth_client, s):
    pid = create(auth_client, s, procedure_type="cag", urgency="elective", weight_kg="60", creatinine_mg_dl="2.0")
    base = f"/api/v1/cathlab/procedures/{pid}/"
    assert auth_client.patch(base, {"vessel_findings": [{"vessel": "LAD", "stenosis_percent": 140}]}, format="json").status_code == 400
    assert auth_client.patch(base, {"vessel_findings": [{"vessel": "RCA", "timi_flow": 5}]}, format="json").status_code == 400
    assert auth_client.patch(base, {"complications": ["Felt dizzy"]}, format="json").status_code == 400
    res = auth_client.patch(base, {"vessel_findings": [{"vessel": "LAD", "segment": "proximal", "stenosis_percent": 90, "timi_flow": 2}],
                                   "contrast_ml": 200, "air_kerma_mgy": 6000, "complications": ["No-reflow"]}, format="json")
    assert res.status_code == 200
    assert res.data["max_contrast_ml"] == 150  # 5 × 60 / 2.0
    assert len(res.data["safety_flags"]) == 2


@pytest.mark.django_db
def test_devices_charges_credit_operator_and_report(auth_client, s):
    pid = create(auth_client, s)
    base = f"/api/v1/cathlab/procedures/{pid}/"
    auth_client.post(base + "start/")
    dev = auth_client.post(base + "add-device/", {"kind": "des", "brand": "Xience", "size": "3.0 × 28 mm", "lot_number": "L123", "vessel": "LAD", "unit_price": "29600"}, format="json")
    assert dev.status_code == 201 and dev.data["devices"][0]["lot_number"] == "L123"
    posted = auth_client.post(base + "post-charges/")
    assert posted.status_code == 200 and posted.data["lines"] == 2
    auth_client.post(base + "post-charges/")  # idempotent
    bill = Bill.objects.get(admission=s["adm"])
    cath_lines = bill.items.filter(source="cathlab")
    assert cath_lines.count() == 2
    assert cath_lines.get(tariff=s["tariff"]).doctor_id == s["doctor"].pk  # payout credit
    assert bill.net_amount == Decimal("119600.00")

    pdf = auth_client.get(base + "report/")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


@pytest.mark.django_db
def test_kpis(auth_client, s):
    for minutes in (70, 110):
        p = CathProcedure.objects.create(hospital=s["hospital"], patient=s["patient"], procedure_type="primary_pci", indication="STEMI",
                                         operator=s["doctor"], status="completed", access_site="right radial", contrast_ml=150)
        now = timezone.now()
        CathProcedure.objects.filter(pk=p.pk).update(started_at=now, door_at=now - datetime.timedelta(minutes=minutes + 10),
                                                     device_at=now - datetime.timedelta(minutes=10))
    k = auth_client.get("/api/v1/cathlab/procedures/kpis/").data
    assert k["procedures"] == 2 and k["primary_pci"]["within_target_percent"] == 50.0
    assert k["primary_pci"]["median_door_to_device_minutes"] == 90 and k["radial_access_percent"] == 100.0
