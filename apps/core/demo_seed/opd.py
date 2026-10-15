"""OPD: doctor schedules, appointments (past + today), visits, encounters
with vitals / notes / diagnoses, prescriptions and consultation bills."""
import secrets
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.appointments.models import Appointment, OPDVisit, Slot, SlotTemplate
from apps.appointments.services import generate_slots
from apps.billing.models import Bill, BillItem, Payment
from apps.billing.services import recalculate_bill
from apps.finance.models import ServiceTariff
from apps.opd.models import ClinicalNote, Diagnosis, Encounter, InvestigationOrder, VitalsReading
from apps.patients.models import Prescription

from .common import NOW, RNG, TODAY, chance, first_page_patients, pick, pick_for, suits

# department -> [(complaint, ICD-10, diagnosis, [medicines])]
CASES = {
    "General Medicine": [("Fever with body ache for 3 days", "R50.9", "Viral fever", ["Tab. Paracetamol 650 mg TDS x 5 days", "ORS sachets as needed"]),
                         ("Increased thirst and frequent urination", "E11.9", "Type 2 diabetes mellitus", ["Tab. Metformin 500 mg BD", "Tab. Glimepiride 1 mg OD"]),
                         ("Headache, BP 160/100", "I10", "Essential hypertension", ["Tab. Amlodipine 5 mg OD", "Tab. Telmisartan 40 mg OD"])],
    "Cardiology": [("Chest discomfort on exertion", "I20.9", "Angina pectoris", ["Tab. Ecosprin 75 mg OD", "Tab. Atorvastatin 40 mg HS", "Tab. Metoprolol 25 mg BD"]),
                   ("Palpitations and breathlessness", "I48.9", "Atrial fibrillation", ["Tab. Metoprolol 50 mg BD", "Tab. Apixaban 5 mg BD"])],
    "Orthopaedics": [("Right knee pain while climbing stairs", "M17.1", "Primary osteoarthritis, right knee", ["Tab. Aceclofenac 100 mg BD x 5 days", "Cap. Calcium + Vit D3 OD"]),
                     ("Low back pain radiating to left leg", "M54.4", "Lumbago with sciatica", ["Tab. Pregabalin 75 mg HS", "Tab. Etoricoxib 60 mg OD x 5 days"])],
    "Paediatrics": [("Cough and cold for 4 days", "J06.9", "Acute upper respiratory infection", ["Syp. Paracetamol 250 mg/5 ml SOS", "Saline nasal drops"]),
                    ("Loose motions since yesterday", "A09", "Acute gastroenteritis", ["ORS 50 ml/kg over 4 h", "Syp. Zinc 20 mg OD x 14 days"])],
    "Obstetrics & Gynaecology": [("Routine antenatal visit, 24 weeks", "Z34.9", "Supervision of normal pregnancy", ["Tab. Ferrous ascorbate OD", "Tab. Calcium 500 mg BD"]),
                                 ("Irregular menstrual cycles", "E28.2", "Polycystic ovarian syndrome", ["Tab. Metformin 500 mg BD", "Lifestyle modification"])],
    "General Surgery": [("Pain in right lower abdomen", "K35.80", "Acute appendicitis", ["Inj. Ceftriaxone 1 g BD", "Plan: laparoscopic appendicectomy"]),
                        ("Swelling in right groin", "K40.90", "Inguinal hernia, right", ["Plan: laparoscopic hernioplasty"])],
    "Neurology": [("Recurrent headaches with aura", "G43.1", "Migraine with aura", ["Tab. Naproxen 500 mg SOS", "Tab. Propranolol 20 mg BD"])],
    "ENT": [("Ear pain and discharge", "H66.9", "Otitis media", ["Cap. Amoxicillin 500 mg TDS x 7 days", "Ciprofloxacin ear drops"])],
    "Dermatology": [("Itchy red patches on arms", "L30.9", "Dermatitis", ["Mometasone cream BD", "Tab. Levocetirizine 5 mg HS"])],
    "Ophthalmology": [("Blurred distance vision", "H52.1", "Myopia", ["Refraction; spectacles prescribed"])],
    "Nephrology": [("Swelling of feet, raised creatinine", "N18.3", "Chronic kidney disease, stage 3", ["Tab. Torsemide 10 mg OD", "Low-salt diet"])],
    "Pulmonology": [("Wheezing at night", "J45.909", "Bronchial asthma", ["Formoterol + Budesonide inhaler BD", "Montelukast 10 mg HS"])],
}
FEES = {"Cardiology": 1000, "Neurology": 1000, "Nephrology": 900, "Orthopaedics": 800, "Obstetrics & Gynaecology": 800, "General Surgery": 800}


def tariffs(h):
    out = {}
    for dept in CASES:
        code = "CONS-" + "".join(w[0] for w in dept.replace("&", "").split()).upper()
        t, _ = ServiceTariff.objects.get_or_create(hospital=h, code=code, defaults={
            "name": f"Consultation – {dept}", "department": dept, "rate": FEES.get(dept, 600),
            "category_rates": {"private": FEES.get(dept, 600) + 200, "insurance": FEES.get(dept, 600) + 300, "staff": FEES.get(dept, 600) // 2},
            "hsn_sac": "999312", "gst_rate": 0})
        out[dept] = t
    return out


def schedules(h, doctors):
    for d in doctors:
        if d.department.name in ("Radiology", "Pathology", "Anaesthesiology"):
            continue
        for wd in range(0, 6):
            start, end = (time(9, 30), time(13, 30)) if d.pk % 2 else (time(15, 0), time(19, 0))
            t, _ = SlotTemplate.objects.get_or_create(hospital=h, doctor=d, weekday=wd, start_time=start, defaults={
                "end_time": end, "slot_duration_minutes": d.default_consultation_minutes})
            generate_slots(t, weeks_ahead=2)


def _slot(h, doctor, day, when):
    """A free slot for the doctor that day, at `when` or the next free quarter-hour."""
    t = datetime.combine(day, when)
    for _ in range(48):
        slot, _ = Slot.objects.get_or_create(hospital=h, doctor=doctor, date=day, start_time=t.time(), defaults={"end_time": (t + timedelta(minutes=15)).time()})
        if not slot.appointments.exists():
            return slot
        t += timedelta(minutes=15)
    raise RuntimeError("no free slot")


def _bill(h, patient, tariff, doctor, day, paid=True, method="upi"):
    bill = Bill.objects.create(hospital=h, patient=patient, status=Bill.Status.UNPAID)
    BillItem.objects.create(bill=bill, description=tariff.name, quantity=1, unit_price=tariff.rate, total_price=0, tariff=tariff,
                            hsn_sac=tariff.hsn_sac, doctor=doctor, service_date=day, source="consultation")
    recalculate_bill(bill)
    if paid:
        Payment.objects.create(hospital=h, bill=bill, amount=bill.net_amount, payment_method=method, transaction_id=f"UPI{RNG.randint(10**9, 10**10 - 1)}" if method == "upi" else "")
        recalculate_bill(bill)
    Bill.objects.filter(pk=bill.pk).update(created_at=timezone.make_aware(datetime.combine(day, time(10, 0))))
    return bill


def _encounter(h, appt, doctor_user, dept_name, done_at):
    enc = Encounter.objects.create(hospital=h, appointment=appt, patient=appt.patient, doctor=appt.doctor, department=appt.doctor.department)
    VitalsReading.objects.create(hospital=h, encounter=enc, height_cm=RNG.randint(150, 182), weight_kg=RNG.randint(48, 92), bp_systolic=RNG.randint(108, 152),
                                 bp_diastolic=RNG.randint(68, 96), pulse=RNG.randint(64, 98), temperature_c=Decimal(str(RNG.choice([36.6, 36.8, 37.0, 37.4, 38.2]))),
                                 spo2=RNG.randint(95, 99), recorded_at=done_at - timedelta(minutes=20))
    complaint, icd, dx, meds = pick(CASES.get(dept_name) or CASES["General Medicine"])
    ClinicalNote.objects.create(hospital=h, encounter=enc, doctor=appt.doctor, chief_complaints=complaint,
                                history="No known drug allergies. " + pick(["Non-smoker.", "Occasional alcohol.", "Known hypertensive on treatment.", "No significant past history."]),
                                examination_findings=pick(["General condition fair, afebrile, chest clear.", "Tenderness present on examination.", "Vitals stable; systemic examination within normal limits."]),
                                finalized_at=done_at, finalized_by=doctor_user)
    Diagnosis.objects.create(hospital=h, encounter=enc, icd_code=icd, description=dx, diagnosis_type="final", created_by=doctor_user, finalized_at=done_at, finalized_by=doctor_user)
    Prescription.objects.create(hospital=h, patient=appt.patient, doctor=doctor_user, encounter=enc, diagnosis=dx, symptoms=complaint,
                                medications=[{"name": m, "dosage": "", "frequency": "", "duration": ""} for m in meds], notes="Review after 7 days or earlier if symptoms worsen.")
    if chance(0.45):
        InvestigationOrder.objects.create(hospital=h, encounter=enc, order_type="lab", description=pick(["CBC, CRP", "HbA1c, Lipid profile", "LFT, KFT", "Thyroid profile"]),
                                          status="completed", created_by=doctor_user)
    return enc


def appointments(h, doctors, patients, users, tariffs_by_dept, days=75):
    """~8–14 OPD appointments a day over the last `days` days plus today's live queue."""
    front = (users.get("receptionist") or [None])[0]
    opd_docs = [d for d in doctors if d.department.name in CASES]
    stats = {"appointments": 0, "visits": 0}
    for back in range(days, 0, -1):
        day = TODAY - timedelta(days=back)
        if day.weekday() == 6:
            continue
        for _ in range(RNG.randint(8, 14)):
            d = pick(opd_docs)
            p = pick_for(patients, d.department.name)
            when = time(RNG.choice([9, 10, 11, 12, 15, 16, 17, 18]), RNG.choice([0, 15, 30, 45]))
            slot = _slot(h, d, day, when)
            status = RNG.choices(["completed", "no_show", "cancelled"], weights=[86, 8, 6])[0]
            start = timezone.make_aware(datetime.combine(day, when))
            a = Appointment.objects.create(hospital=h, registration_token=secrets.token_urlsafe(24), patient=p, doctor=d, slot=slot, status=status, source=pick(["walk_in", "telephone", "website", "whatsapp", "crm", "portal"]),
                                           reason="", booked_by=front, consent_captured=True, checked_in_at=start if status == "completed" else None,
                                           consult_started_at=start + timedelta(minutes=RNG.randint(5, 25)) if status == "completed" else None,
                                           completed_at=start + timedelta(minutes=RNG.randint(30, 50)) if status == "completed" else None,
                                           no_show_at=start + timedelta(hours=1) if status == "no_show" else None, cancelled_at=start - timedelta(hours=4) if status == "cancelled" else None)
            Appointment.objects.filter(pk=a.pk).update(created_at=start - timedelta(days=RNG.randint(0, 4)))
            stats["appointments"] += 1
            if status == "completed":
                bill = _bill(h, p, tariffs_by_dept[d.department.name], d, day, paid=chance(0.96), method=pick(["upi", "upi", "cash", "card"]))
                v = OPDVisit.objects.create(hospital=h, patient=p, doctor=d, department=d.department, appointment=a, visit_type=pick(["new", "follow_up", "free_follow_up"]),
                                            is_new_patient=chance(0.3), payer_type=pick(["self", "self", "self", "insurance", "corporate"]), bill=bill, registered_by=front)
                OPDVisit.objects.filter(pk=v.pk).update(created_at=start)
                _encounter(h, a, d.user, d.department.name, a.completed_at)
                stats["visits"] += 1
    # Today's live OPD queue: morning clinics, a few patients per doctor; earlier
    # slots are further along (completed → in consultation → checked in → booked)
    page = first_page_patients(h)  # the calendar resolves names from the first patient page
    load, plan = {}, []
    for p in page:
        options = []
        for d in opd_docs:
            if not suits(p, d.department.name) or load.get(d.pk, 0) >= 4:
                continue
            s_ = Slot.objects.filter(hospital=h, doctor=d, date=TODAY, appointments__isnull=True, start_time__lt=time(13, 0)).exclude(
                pk__in=[x[2].pk for x in plan]).order_by("start_time").first()
            if s_:
                options.append((load.get(d.pk, 0), s_.start_time, d.pk, d, s_))
        if not options:
            continue
        _, _, _, d, slot = min(options, key=lambda o: o[:3])
        load[d.pk] = load.get(d.pk, 0) + 1
        plan.append((p, d, slot))
    by_doc = {}
    for p, d, slot in sorted(plan, key=lambda x: x[2].start_time):
        by_doc.setdefault(d.pk, []).append((p, d, slot))
    for rows in by_doc.values():
        n = len(rows)
        for k, (p, d, slot) in enumerate(rows):
            if n == 1:
                status = pick(["completed", "in_consult", "checked_in", "booked"])
            else:  # per doctor: done … one in consultation … the last one waiting or booked
                status = "completed" if k < n - 2 else "in_consult" if k == n - 2 else pick(["checked_in", "confirmed", "booked"])
            start = timezone.make_aware(datetime.combine(TODAY, slot.start_time))
            a = Appointment.objects.create(hospital=h, registration_token=secrets.token_urlsafe(24), patient=p, doctor=d, slot=slot, status=status, source=pick(["walk_in", "telephone", "website", "whatsapp"]), booked_by=front,
                                           consent_captured=True, queue_token=k + 1,
                                           checked_in_at=start - timedelta(minutes=10) if status in ("completed", "in_consult", "checked_in") else None,
                                           consult_started_at=start if status in ("completed", "in_consult") else None, completed_at=start + timedelta(minutes=14) if status == "completed" else None)
            if status in ("completed", "in_consult", "checked_in"):
                bill = _bill(h, p, tariffs_by_dept[d.department.name], d, TODAY, paid=True)
                OPDVisit.objects.create(hospital=h, patient=p, doctor=d, department=d.department, appointment=a, visit_type=pick(["new", "follow_up"]), payer_type="self", bill=bill, registered_by=front)
            if status == "completed":
                _encounter(h, a, d.user, d.department.name, a.completed_at)
            stats["appointments"] += 1
    return stats
