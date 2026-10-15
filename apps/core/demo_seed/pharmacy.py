"""Pharmacy: suppliers, formulary (generic brand-neutral names), batches with
MRP / expiry / stock, and dispensing to inpatients and OPD patients — each
dispense charged to the patient's bill through the app's own billing path."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.ipd.models import Admission
from apps.pharmacy.billing import bill_dispense
from apps.pharmacy.models import DispenseRecord, Medicine, MedicineBatch, Supplier
from apps.pharmacy.services import dispense_medicine

from .common import NOW, RNG, TODAY, pick

# name, generic, form, strength, category, mrp, gst, flags
MEDICINES = [
    ("Paracetamol 650", "Paracetamol", "tablet", "650 mg", "Analgesic / Antipyretic", 32, 12, {}),
    ("Amoxicillin-Clavulanate 625", "Amoxicillin + Clavulanic acid", "tablet", "625 mg", "Antibiotic", 210, 12, {"is_restricted_antimicrobial": False}),
    ("Azithromycin 500", "Azithromycin", "tablet", "500 mg", "Antibiotic", 118, 12, {}),
    ("Ceftriaxone 1 g Inj", "Ceftriaxone", "injection", "1 g", "Antibiotic", 68, 12, {}),
    ("Meropenem 1 g Inj", "Meropenem", "injection", "1 g", "Antibiotic (reserve)", 1450, 12, {"is_restricted_antimicrobial": True}),
    ("Pantoprazole 40", "Pantoprazole", "tablet", "40 mg", "Proton pump inhibitor", 95, 12, {}),
    ("Pantoprazole 40 mg Inj", "Pantoprazole", "injection", "40 mg", "Proton pump inhibitor", 58, 12, {}),
    ("Ondansetron 4 mg Inj", "Ondansetron", "injection", "4 mg", "Antiemetic", 22, 12, {}),
    ("Metformin 500", "Metformin", "tablet", "500 mg", "Antidiabetic", 28, 12, {}),
    ("Glimepiride 1", "Glimepiride", "tablet", "1 mg", "Antidiabetic", 64, 12, {}),
    ("Insulin Glargine 100 IU/ml", "Insulin glargine", "injection", "3 ml pen", "Antidiabetic (insulin)", 690, 5, {"is_high_risk": True, "storage": "2–8 °C"}),
    ("Amlodipine 5", "Amlodipine", "tablet", "5 mg", "Antihypertensive", 26, 12, {}),
    ("Telmisartan 40", "Telmisartan", "tablet", "40 mg", "Antihypertensive", 82, 12, {}),
    ("Metoprolol 25", "Metoprolol succinate", "tablet", "25 mg", "Beta blocker", 74, 12, {}),
    ("Atorvastatin 40", "Atorvastatin", "tablet", "40 mg", "Statin", 160, 12, {}),
    ("Aspirin 75", "Aspirin", "tablet", "75 mg", "Antiplatelet", 12, 12, {}),
    ("Clopidogrel 75", "Clopidogrel", "tablet", "75 mg", "Antiplatelet", 92, 12, {}),
    ("Heparin 5000 IU Inj", "Heparin sodium", "injection", "5000 IU/ml", "Anticoagulant", 145, 12, {"is_high_risk": True}),
    ("Potassium Chloride 15% Inj", "Potassium chloride", "injection", "10 ml", "Electrolyte", 38, 12, {"is_high_risk": True, "is_lasa": True, "lasa_pair": "Sodium chloride 0.9%"}),
    ("Normal Saline 500 ml", "Sodium chloride 0.9%", "other", "500 ml", "IV fluid", 34, 12, {}),
    ("Ringer Lactate 500 ml", "Compound sodium lactate", "other", "500 ml", "IV fluid", 38, 12, {}),
    ("Morphine 10 mg Inj", "Morphine sulphate", "injection", "10 mg/ml", "Opioid analgesic", 52, 12, {"is_high_risk": True, "is_controlled": True}),
    ("Tramadol 50", "Tramadol", "capsule", "50 mg", "Analgesic", 58, 12, {"is_controlled": True}),
    ("Diclofenac 75 mg Inj", "Diclofenac", "injection", "75 mg", "NSAID", 18, 12, {}),
    ("Aceclofenac 100", "Aceclofenac", "tablet", "100 mg", "NSAID", 64, 12, {}),
    ("Levocetirizine 5", "Levocetirizine", "tablet", "5 mg", "Antihistamine", 48, 12, {}),
    ("Montelukast 10", "Montelukast", "tablet", "10 mg", "Antiasthmatic", 145, 12, {}),
    ("Salbutamol Respules", "Salbutamol", "other", "2.5 mg", "Bronchodilator", 24, 12, {}),
    ("Budesonide-Formoterol Inhaler", "Budesonide + Formoterol", "other", "200/6 mcg", "Inhaled steroid", 465, 12, {}),
    ("Paracetamol Syrup", "Paracetamol", "syrup", "250 mg/5 ml", "Analgesic (paediatric)", 46, 12, {}),
    ("ORS Sachet", "Oral rehydration salts", "other", "21 g", "Rehydration", 21, 5, {"is_emergency": False}),
    ("Ferrous Ascorbate", "Ferrous ascorbate + folic acid", "tablet", "100 mg", "Haematinic", 118, 12, {}),
    ("Calcium + Vitamin D3", "Calcium carbonate + cholecalciferol", "tablet", "500 mg", "Supplement", 98, 12, {}),
    ("Adrenaline 1 mg Inj", "Adrenaline", "injection", "1 mg/ml", "Emergency", 24, 12, {"is_emergency": True, "is_high_risk": True}),
    ("Atropine 0.6 mg Inj", "Atropine", "injection", "0.6 mg/ml", "Emergency", 9, 12, {"is_emergency": True}),
    ("Hydrocortisone 100 mg Inj", "Hydrocortisone", "injection", "100 mg", "Corticosteroid", 46, 12, {"is_emergency": True}),
    ("Mometasone Cream", "Mometasone furoate", "ointment", "0.1% 15 g", "Topical steroid", 185, 12, {}),
    ("Ciprofloxacin Ear Drops", "Ciprofloxacin", "other", "0.3% 10 ml", "Antibiotic (otic)", 38, 12, {}),
    ("Pregabalin 75", "Pregabalin", "capsule", "75 mg", "Neuropathic pain", 168, 12, {}),
    ("Torsemide 10", "Torsemide", "tablet", "10 mg", "Diuretic", 78, 12, {}),
]
SUPPLIERS = [("Sahyadri Pharma Distributors", "Mr. Nitin Kale"), ("Deccan Medical Agencies", "Ms. Asha Deshmukh"), ("Western India Surgicals", "Mr. Vinay Joshi"),
             ("Pune Healthcare Supplies", "Mr. Ajay More"), ("Lifeline Drug House", "Ms. Usha Patil")]


def run(ctx):
    h, pts, users = ctx["h"], ctx["pts"], ctx["users"]
    pharmacist = (users.get("pharmacist") or [None])[0]
    sups = [Supplier.objects.get_or_create(hospital=h, name=n, defaults={"contact_person": c, "phone": "+91 20 6000 " + str(1000 + i), "email": f"orders{i}@example.com"})[0]
            for i, (n, c) in enumerate(SUPPLIERS)]
    meds = []
    for name, generic, form, strength, cat, mrp, gst, flags in MEDICINES:
        m, _ = Medicine.objects.get_or_create(hospital=h, name=name, defaults={"generic_name": generic, "form": form, "strength": strength, "category": cat,
                                                                               "unit": "strip of 10" if form in ("tablet", "capsule") else "unit", "reorder_level": RNG.choice([50, 100, 200]),
                                                                               "gst_rate": gst, "route": "IV" if form == "injection" else "Oral", **flags})
        meds.append((m, mrp))
        for b in range(RNG.randint(1, 3)):
            expiry = TODAY + timedelta(days=RNG.choice([25, 60, 200, 420, 640]))
            qty = RNG.choice([8, 30, 120, 240, 400, 600])
            MedicineBatch.objects.get_or_create(hospital=h, medicine=m, batch_number=f"{name[:3].upper()}{RNG.randint(1000, 9999)}{b}", defaults={
                "expiry_date": expiry, "quantity_available": qty, "mrp": Decimal(mrp), "purchase_price": (Decimal(mrp) * Decimal("0.62")).quantize(Decimal("0.01")),
                "supplier": pick(sups)})
    n = 0
    inpatients = list(Admission.objects.filter(hospital=h, status="admitted").select_related("patient"))
    for adm in inpatients:
        for _ in range(RNG.randint(2, 5)):
            m, _mrp = pick(meds)
            batch = m.batches.filter(quantity_available__gt=10, expiry_date__gt=TODAY).first()
            if batch is None:
                continue
            rec = dispense_medicine(hospital=h, batch=batch, quantity=RNG.randint(1, 6), dispensed_by=pharmacist)
            rec.patient = adm.patient
            rec.save(update_fields=["patient"])
            bill_dispense(rec)
            n += 1
    for p in pick(pts[150:], 40):
        m, _mrp = pick(meds)
        batch = m.batches.filter(quantity_available__gt=10, expiry_date__gt=TODAY).first()
        if batch is None:
            continue
        rec = dispense_medicine(hospital=h, batch=batch, quantity=RNG.randint(1, 3), dispensed_by=pharmacist)
        rec.patient = p
        rec.save(update_fields=["patient"])
        bill_dispense(rec)
        when = NOW - timedelta(days=RNG.randint(0, 20), hours=RNG.randint(0, 8))
        DispenseRecord.objects.filter(pk=rec.pk).update(dispensed_at=when)
        n += 1
    return {"medicines": len(meds), "suppliers": len(sups), "dispenses": n}
