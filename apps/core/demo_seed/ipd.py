"""IPD: wards, rooms, beds, bed-charge tariffs, current inpatients, discharged
admissions with discharge summaries, progress notes, nursing records."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from apps.billing.bed_charges import post_bed_charges
from apps.billing.models import BedChargeRule, Bill, Payment, PatientAdvance
from apps.billing.services import recalculate_bill
from apps.facilities.models import Bed, Room, Ward
from apps.finance.models import ServiceTariff
from apps.ipd.models import Admission, BedAllocation, DischargeSummary, DoctorProgressNote
from apps.ipd.services import admit_patient, discharge_patient
from apps.nursing.models import IntakeOutput, MedicationAdministration, NursingNote

from .common import NOW, RNG, TODAY, chance, days_ago, first_page_patients, pick

WARDS = [
    # name, ward_type, floor, dept, rooms, beds per room, bed_type, rent
    ("General Ward – Male", "general", "1st Floor", "General Medicine", 4, 4, "general", 1500),
    ("General Ward – Female", "general", "1st Floor", "General Medicine", 4, 4, "general", 1500),
    ("Semi-Private Ward", "semi_private", "2nd Floor", "General Surgery", 6, 2, "semi_private", 3000),
    ("Private Rooms", "private", "3rd Floor", "Cardiology", 8, 1, "private", 5500),
    ("Maternity Ward", "semi_private", "2nd Floor", "Obstetrics & Gynaecology", 4, 2, "semi_private", 3200),
    ("Paediatric Ward", "general", "1st Floor", "Paediatrics", 3, 3, "general", 1800),
    ("Medical ICU", "icu", "4th Floor", "General Medicine", 1, 8, "icu", 12000),
    ("Cardiac ICU", "icu", "4th Floor", "Cardiology", 1, 6, "icu", 14000),
]

# unique room codes per ward (GM101-A, GF101-A, ...) so no two beds share a label
ROOM_PREFIX = {"General Ward – Male": "GM1", "General Ward – Female": "GF1", "Semi-Private Ward": "SP2", "Private Rooms": "PR3",
               "Maternity Ward": "MT2", "Paediatric Ward": "PD1", "Medical ICU": "MICU", "Cardiac ICU": "CICU"}

IPD_CASES = [
    ("Cardiology", "Acute coronary syndrome – NSTEMI", "Coronary angiography; PTCA to LAD with drug-eluting stent", "Tab. Ecosprin 75 mg OD, Tab. Ticagrelor 90 mg BD, Tab. Atorvastatin 80 mg HS"),
    ("General Surgery", "Acute calculous cholecystitis", "Laparoscopic cholecystectomy", "Tab. Pantoprazole 40 mg OD x 14 days, Tab. Paracetamol 650 mg SOS"),
    ("Orthopaedics", "Osteoarthritis right knee, Kellgren–Lawrence grade IV", "Total knee replacement (right)", "Tab. Apixaban 2.5 mg BD x 14 days, physiotherapy protocol"),
    ("General Medicine", "Community-acquired pneumonia", "IV antibiotics, nebulisation", "Tab. Amoxicillin-Clavulanate 625 mg TDS x 5 days"),
    ("General Medicine", "Dengue fever with thrombocytopenia", "Supportive care, platelet monitoring", "Tab. Paracetamol 650 mg SOS, oral fluids 3 L/day"),
    ("Obstetrics & Gynaecology", "Term pregnancy, LSCS", "Lower segment caesarean section", "Tab. Iron + Folic acid OD, Tab. Calcium 500 mg BD"),
    ("Neurology", "Acute ischaemic stroke, left MCA territory", "Thrombolysis evaluation, antiplatelets, physiotherapy", "Tab. Clopidogrel 75 mg OD, Tab. Atorvastatin 40 mg HS"),
    ("Nephrology", "Acute kidney injury on CKD", "Haemodialysis x 2 sessions", "Tab. Sodium bicarbonate 500 mg TDS, fluid restriction 1 L/day"),
    ("Paediatrics", "Bronchiolitis", "Oxygen support, nebulisation", "Saline nebulisation QID, Syp. Paracetamol SOS"),
    ("Pulmonology", "Acute exacerbation of COPD", "Nebulisation, steroids, NIV support", "Inhaler Tiotropium OD, Tab. Prednisolone taper"),
]


def wards(h, depts):
    beds = []
    for name, wtype, floor, dept, rooms, per_room, btype, rent in WARDS:
        w, _ = Ward.objects.get_or_create(hospital=h, name=name, defaults={"ward_type": wtype, "floor": floor, "department": depts[dept]})
        rtype = {"general": "general", "semi_private": "semi_private", "private": "private", "icu": "general"}[wtype if wtype != "ot_prep" else "general"]
        for r in range(1, rooms + 1):
            room, _ = Room.objects.get_or_create(hospital=h, ward=w, room_number=f"{ROOM_PREFIX[name]}{r:02d}", defaults={"room_type": rtype})
            for b in range(1, per_room + 1):
                bed, _ = Bed.objects.get_or_create(hospital=h, room=room, bed_number=f"{room.room_number}-{chr(64 + b)}", defaults={"bed_type": btype})
                beds.append(bed)
        t, _ = ServiceTariff.objects.get_or_create(hospital=h, code=f"BED-{name[:3].upper()}{rent}", defaults={
            "name": f"Bed charges – {name}", "department": "Room", "rate": rent, "category_rates": {"insurance": int(rent * 1.2), "staff": int(rent * 0.5)}, "hsn_sac": "999311"})
        BedChargeRule.objects.get_or_create(hospital=h, name=f"{name} – room rent", ward=w, defaults={"tariff": t})
        nursing, _ = ServiceTariff.objects.get_or_create(hospital=h, code=f"NURS-{name[:3].upper()}", defaults={
            "name": f"Nursing charges – {name}", "department": "Nursing", "rate": 800 if wtype != "icu" else 2500, "hsn_sac": "999311"})
        BedChargeRule.objects.get_or_create(hospital=h, name=f"{name} – nursing", ward=w, defaults={"tariff": nursing})
    # a few beds out of service
    for bed in pick(beds, 4):
        if bed.status == Bed.Status.AVAILABLE:
            Bed.objects.filter(pk=bed.pk).update(status=pick([Bed.Status.MAINTENANCE, Bed.Status.CLEANING]))
    return beds


def _doctor_for(docs, dept):
    return next((d for d in docs if d.department.name == dept), docs[0])


def _notes(h, adm, doc, nurse, days):
    ct = ContentType.objects.get_for_model(Admission)
    for i in range(max(1, days)):
        when = adm.admitted_at + timedelta(days=i, hours=10)
        if when > NOW:
            break
        n = DoctorProgressNote.objects.create(hospital=h, admission=adm, doctor=doc, note=pick([
            "Patient comfortable, afebrile. Vitals stable. Continue current management.",
            "Pain reduced. Tolerating oral diet. Mobilised with support.",
            "Reviewed investigations; parameters improving. Plan to step down IV medication.",
            "Wound healthy, no discharge. Dressing done. Continue antibiotics."]), finalized_at=when, finalized_by=doc.user)
        DoctorProgressNote.objects.filter(pk=n.pk).update(created_at=when)
        if nurse:
            NursingNote.objects.create(hospital=h, content_type=ct, object_id=str(adm.pk), nurse=nurse, note=pick([
                "Vitals charted 4-hourly. IV line patent. Patient slept well.", "Medications given as per chart. Intake adequate.",
                "Ambulated in corridor with assistance. Fall-risk precautions in place."]))
            IntakeOutput.objects.create(hospital=h, content_type=ct, object_id=str(adm.pk), recorded_by=nurse, intake_ml=RNG.randint(1600, 2600), output_ml=RNG.randint(1200, 2200), recorded_at=when)
    if nurse:
        for med in ["Inj. Pantoprazole 40 mg IV", "Inj. Ceftriaxone 1 g IV", "Tab. Paracetamol 650 mg"]:
            MedicationAdministration.objects.create(hospital=h, content_type=ct, object_id=str(adm.pk), medication_name=med, dose=med.split()[-2] if len(med.split()) > 2 else "",
                                                    nurse=nurse, patient=adm.patient, route="IV" if "Inj" in med else "Oral", status="given",
                                                    administered_at=NOW - timedelta(hours=RNG.randint(1, 20)), identity_method="wristband_scan", identity_verified=True)


def _advance(h, adm, amount, user):
    PatientAdvance.objects.create(hospital=h, patient=adm.patient, kind="deposit", amount=amount, payment_method=pick(["upi", "card", "cash"]), recorded_by=user)


def _age(p):
    return (TODAY - p.date_of_birth).days // 365 if p.date_of_birth else 40


def _case_for(ward_name):
    """Clinically plausible case for the ward: maternity → obstetric, paediatric → paediatric, else adult cases."""
    if ward_name == "Maternity Ward":
        return next(c for c in IPD_CASES if c[0] == "Obstetrics & Gynaecology")
    if ward_name == "Paediatric Ward":
        return next(c for c in IPD_CASES if c[0] == "Paediatrics")
    if ward_name == "Cardiac ICU":
        return pick([c for c in IPD_CASES if c[0] in ("Cardiology", "Neurology")])
    return pick([c for c in IPD_CASES if c[0] not in ("Obstetrics & Gynaecology", "Paediatrics")])


def _fits(ward_name, p):
    """Who can occupy a bed in this ward: gender-specific general wards, maternity for women 20–42, paediatric for under-14s."""
    age = _age(p)
    if ward_name == "Paediatric Ward":
        return age < 14
    if age < 14:
        return False
    if ward_name == "Maternity Ward":
        return p.gender == "female" and 20 <= age <= 42
    if ward_name == "General Ward – Male":
        return p.gender == "male"
    if ward_name == "General Ward – Female":
        return p.gender == "female"
    return True


def _take(pool, ward_name):
    for i, p in enumerate(pool):
        if _fits(ward_name, p):
            return pool.pop(i)
    return None


def run(ctx):
    h, depts, docs, pts, users = ctx["h"], ctx["depts"], ctx["docs"], ctx["pts"], ctx["users"]
    beds = wards(h, depts)
    nurses = users.get("nurse", [])
    billing_user = (users.get("billing_executive") or [None])[0]
    page_ids = {p.pk for p in first_page_patients(h)}
    pool = [p for p in pts[:180] if p.pk not in page_ids]  # the first patient page is kept for today's OPD queue
    RNG.shuffle(pool)
    free = [b for b in Bed.objects.filter(hospital=h, status=Bed.Status.AVAILABLE).select_related("room__ward")]
    icu_beds = [b for b in free if b.bed_type == "icu"]
    ward_beds = [b for b in free if b.bed_type != "icu"]
    RNG.shuffle(ward_beds)
    RNG.shuffle(icu_beds)
    current, discharged = [], []
    # ~70 % occupancy now
    for bed in ward_beds[: int(len(ward_beds) * 0.68)] + icu_beds[:9]:
        p = _take(pool, bed.room.ward.name)
        if p is None:
            continue
        dept, dx, proc, meds = _case_for(bed.room.ward.name)
        doc = _doctor_for(docs, dept)
        adm = admit_patient(hospital=h, patient=p, admitting_doctor=doc, bed=bed, admission_type=pick(["planned", "planned", "emergency"]), department=depts[dept], admission_diagnosis=dx)
        stay = RNG.randint(1, 7)
        when = days_ago(stay)
        Admission.objects.filter(pk=adm.pk).update(admitted_at=when, expected_discharge_date=TODAY + timedelta(days=RNG.randint(0, 4)))
        BedAllocation.objects.filter(admission=adm).update(allocated_at=when)
        adm.refresh_from_db()
        _notes(h, adm, doc, pick(nurses) if nurses else None, stay)
        _advance(h, adm, Decimal(pick([10000, 20000, 25000, 50000])), billing_user)
        bill, _ = post_bed_charges(adm)
        current.append(adm)
    # discharged over the last 60 days
    for _ in range(70):
        bed = Bed.objects.filter(hospital=h, status=Bed.Status.AVAILABLE, bed_type__in=["general", "semi_private", "private"]).select_related("room__ward").order_by("?").first()
        if bed is None:
            continue
        ward_name = bed.room.ward.name
        p = pick([q for q in pts if _fits(ward_name, q)])
        if p.admissions.filter(status="admitted").exists():
            continue
        dept, dx, proc, meds = _case_for(ward_name)
        doc = _doctor_for(docs, dept)
        adm = admit_patient(hospital=h, patient=p, admitting_doctor=doc, bed=bed, department=depts[dept], admission_diagnosis=dx)
        DischargeSummary.objects.create(hospital=h, admission=adm, final_diagnosis=dx, procedures_performed=proc,
                                        treatment_summary=f"Admitted with {dx.lower()}. Managed with {proc.lower()}. Recovery uneventful.",
                                        discharge_medications=meds, follow_up_instructions="Review in OPD after 7 days with reports. Return immediately if fever, pain or breathlessness.",
                                        discharge_type="routine", prepared_by=doc.user, finalized_at=NOW, finalized_by=doc.user)
        discharge_patient(adm)
        stay = RNG.randint(2, 6)
        start = days_ago(RNG.randint(stay + 1, 60))
        end = start + timedelta(days=stay, hours=RNG.randint(1, 6))
        Admission.objects.filter(pk=adm.pk).update(admitted_at=start, discharged_at=end)
        BedAllocation.objects.filter(admission=adm).update(allocated_at=start, released_at=end)
        adm.refresh_from_db()
        bill, _ = post_bed_charges(adm)
        bill.status = Bill.Status.UNPAID
        bill.save(update_fields=["status"])
        recalculate_bill(bill)
        if bill.net_amount:
            Payment.objects.create(hospital=h, bill=bill, amount=bill.net_amount if chance(0.85) else bill.net_amount / 2, payment_method=pick(["upi", "card", "bank_transfer", "insurance"]))
            recalculate_bill(bill)
        Bill.objects.filter(pk=bill.pk).update(created_at=start)
        discharged.append(adm)
    return {"beds": len(beds), "inpatients": len(current), "discharged": len(discharged)}
