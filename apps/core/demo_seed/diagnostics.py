"""Laboratory (catalogue, orders, samples, results) and radiology (procedures,
equipment, orders, reports). Results are plausible demonstration values."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.laboratory.models import LabOrder, LabReportTemplate, LabResult, LabSettings, LabTest, LabTestPackage, SampleCollection
from apps.radiology.models import RadiologyEquipment, RadiologyOrder, RadiologyProcedure, RadiologyReport, RadiologyTemplate

from .common import NOW, RNG, TODAY, chance, days_ago, pick, pick_for

# name, code, department, unit, low, high, price, sample, tat, nabl
TESTS = [
    ("Haemoglobin", "HB", "hematology", "g/dL", 12.0, 16.0, 150, "EDTA blood", 4, True),
    ("Total Leucocyte Count", "TLC", "hematology", "/cumm", 4000, 11000, 150, "EDTA blood", 4, True),
    ("Platelet Count", "PLT", "hematology", "lakh/cumm", 1.5, 4.5, 200, "EDTA blood", 4, True),
    ("ESR", "ESR", "hematology", "mm/hr", 0, 20, 120, "EDTA blood", 6, True),
    ("Fasting Blood Sugar", "FBS", "biochemistry", "mg/dL", 70, 100, 100, "Fluoride plasma", 3, True),
    ("Post-Prandial Blood Sugar", "PPBS", "biochemistry", "mg/dL", 70, 140, 100, "Fluoride plasma", 3, True),
    ("HbA1c", "HBA1C", "biochemistry", "%", 4.0, 5.6, 550, "EDTA blood", 6, True),
    ("Serum Creatinine", "CREAT", "biochemistry", "mg/dL", 0.6, 1.3, 200, "Serum", 4, True),
    ("Blood Urea", "UREA", "biochemistry", "mg/dL", 15, 40, 180, "Serum", 4, True),
    ("Serum Sodium", "NA", "biochemistry", "mmol/L", 135, 145, 250, "Serum", 4, True),
    ("Serum Potassium", "K", "biochemistry", "mmol/L", 3.5, 5.1, 250, "Serum", 4, True),
    ("Total Cholesterol", "CHOL", "biochemistry", "mg/dL", 0, 200, 250, "Serum", 6, True),
    ("Triglycerides", "TG", "biochemistry", "mg/dL", 0, 150, 250, "Serum", 6, True),
    ("SGPT (ALT)", "ALT", "biochemistry", "U/L", 7, 56, 200, "Serum", 4, True),
    ("SGOT (AST)", "AST", "biochemistry", "U/L", 10, 40, 200, "Serum", 4, True),
    ("Total Bilirubin", "TBIL", "biochemistry", "mg/dL", 0.2, 1.2, 180, "Serum", 4, True),
    ("TSH", "TSH", "biochemistry", "µIU/mL", 0.4, 4.0, 400, "Serum", 8, True),
    ("C-Reactive Protein", "CRP", "serology", "mg/L", 0, 6, 450, "Serum", 4, True),
    ("Dengue NS1 Antigen", "NS1", "serology", "", None, None, 900, "Serum", 4, False),
    ("Widal Test", "WIDAL", "serology", "", None, None, 300, "Serum", 6, False),
    ("Urine Routine & Microscopy", "URM", "pathology", "", None, None, 200, "Urine", 3, True),
    ("Urine Culture & Sensitivity", "UCS", "microbiology", "", None, None, 800, "Urine", 72, False),
    ("Blood Culture", "BC", "microbiology", "", None, None, 1200, "Blood (culture bottle)", 72, False),
    ("Troponin I", "TROP", "biochemistry", "ng/mL", 0, 0.04, 1100, "Serum", 2, True),
]
PACKAGES = [("Diabetes Panel", ["FBS", "PPBS", "HBA1C", "CREAT"], 850), ("Lipid & Liver Panel", ["CHOL", "TG", "ALT", "AST", "TBIL"], 950),
            ("Fever Panel", ["HB", "TLC", "PLT", "CRP", "NS1", "WIDAL"], 1800), ("Kidney Function Panel", ["CREAT", "UREA", "NA", "K"], 750)]

PROCEDURES = [("Chest X-ray PA view", "xray", 450, False), ("X-ray Knee AP/Lateral", "xray", 600, False), ("USG Abdomen & Pelvis", "usg", 1500, False),
              ("USG Obstetric (Level II)", "usg", 2200, False), ("CT Brain (plain)", "ct", 3500, False), ("CT Abdomen with contrast", "ct", 6500, True),
              ("HRCT Chest", "ct", 4500, False), ("MRI Brain with contrast", "mri", 8500, True), ("MRI Lumbar Spine", "mri", 7500, False),
              ("2D Echocardiography", "usg", 2500, False), ("Doppler – Lower Limb Venous", "usg", 2800, False)]
FINDINGS = {
    "Chest X-ray PA view": ("Lung fields are clear. Cardiac silhouette within normal limits. Costophrenic angles clear.", "No active cardiopulmonary abnormality."),
    "USG Abdomen & Pelvis": ("Liver normal in size with mildly increased echotexture. Gall bladder shows a 9 mm calculus. CBD not dilated. Kidneys normal.", "Fatty liver grade I. Cholelithiasis."),
    "CT Brain (plain)": ("No intracranial haemorrhage or mass effect. Ventricles normal. Grey–white differentiation preserved.", "No acute intracranial abnormality."),
    "MRI Lumbar Spine": ("Disc desiccation at L4–L5 with posterior disc bulge indenting the thecal sac and narrowing the left neural foramen.", "L4–L5 disc bulge with left foraminal narrowing."),
    "2D Echocardiography": ("Normal chamber dimensions. LVEF 55%. No regional wall motion abnormality. Valves normal.", "Normal study. LVEF 55%."),
}


def catalogue(h):
    tmpl, _ = LabReportTemplate.objects.get_or_create(hospital=h, name="Standard laboratory report", defaults={
        "department": "biochemistry", "header_text": "Department of Laboratory Medicine", "footer_text": "End of report. Results relate to the sample tested.",
        "signatory_designation": "Consultant Pathologist", "show_previous_result": True})
    tests = {}
    for name, code, dept, unit, lo, hi, price, sample, tat, nabl in TESTS:
        t, _ = LabTest.objects.get_or_create(hospital=h, code=code, defaults={
            "name": name, "department": dept, "unit": unit, "price": price, "sample_type": sample, "tat_hours": tat, "is_nabl_accredited": nabl,
            "ref_low": lo, "ref_high": hi, "reference_range": f"{lo} – {hi} {unit}".strip() if lo is not None else "Negative / Not detected",
            "report_template": tmpl, "container": "Lavender top" if "EDTA" in sample else "Red top" if sample == "Serum" else "Sterile container",
            "critical_low": lo * Decimal("0.6") if isinstance(lo, Decimal) else None, "method": pick(["Spectrophotometry", "Automated analyser", "Immunoassay (CLIA)"])})
        tests[code] = t
    for name, codes, price in PACKAGES:
        pkg, created = LabTestPackage.objects.get_or_create(hospital=h, name=name, defaults={"price": price})
        if created:
            pkg.tests.set([tests[c] for c in codes])
    LabSettings.objects.get_or_create(hospital=h, defaults={"nabl_certificate_number": "MC-0000 (demo)", "nabl_valid_until": TODAY + timedelta(days=500),
                                                             "auto_dispatch": True, "dispatch_channels": ["whatsapp", "email"], "dispatch_delay_minutes": 30})
    return tests


def _value(t):
    if t.ref_low is None:
        return pick(["Negative", "Negative", "Non-reactive", "Positive"]) if t.code in ("NS1", "WIDAL") else pick(["No growth after 48 hours", "Within normal limits"]), "normal"
    lo, hi = float(t.ref_low), float(t.ref_high)
    span = hi - lo or hi or 1
    r = RNG.random()
    v = lo + span * (RNG.uniform(0.1, 0.9) if r < 0.72 else RNG.uniform(1.05, 1.6) if r < 0.92 else RNG.uniform(-0.4, -0.05))
    v = max(v, 0)
    flag = "normal" if lo <= v <= hi else ("high" if v > hi else "low")
    fmt = f"{v:.0f}" if hi >= 100 else f"{v:.1f}" if hi >= 5 else f"{v:.2f}"
    return fmt, flag


def lab_orders(h, pts, docs, users, tests):
    techs = users.get("lab_technician", [None])
    patho = next((d for d in docs if d.department.name == "Pathology"), docs[0])
    groups = [["HB", "TLC", "PLT", "ESR"], ["FBS", "PPBS", "HBA1C"], ["CREAT", "UREA", "NA", "K"], ["CHOL", "TG", "ALT", "AST"], ["TSH"], ["CRP", "NS1"],
              ["URM"], ["TROP"], ["UCS"], ["HB", "TLC", "PLT", "CRP"]]
    seq = {}
    n = 0
    for back in list(range(45, 0, -1)) + [0, 0, 0]:
        for _ in range(RNG.randint(4, 7)):
            p = pick(pts)
            when = days_ago(back) if back else NOW - timedelta(hours=RNG.randint(0, 6))
            day = timezone.localdate(when)
            seq[day] = seq.get(day, 0) + 1
            codes = pick(groups)
            if back == 0:
                status = pick(["ordered", "sample_collected", "processing", "resulted", "verified"])
            else:
                status = pick(["verified"] * 8 + ["resulted"])
            o = LabOrder.objects.create(hospital=h, patient=p, status=status, ordered_by=pick(docs).user, order_number=f"LAB{day:%y%m%d}{seq[day]:04d}",
                                        priority=pick(["routine", "routine", "routine", "urgent", "stat"]), department=tests[codes[0]].department,
                                        clinical_notes=pick(["Fever 3 days", "Diabetes follow-up", "Pre-operative work-up", "Annual health check", "Chest pain evaluation"]))
            LabOrder.objects.filter(pk=o.pk).update(ordered_at=when)
            o.ordered_tests.set([tests[c] for c in codes])
            if status != "ordered":
                SampleCollection.objects.create(hospital=h, lab_order=o, sample_type=tests[codes[0]].sample_type, status="completed" if status in ("resulted", "verified") else "received",
                                                barcode=f"S{day:%y%m%d}{seq[day]:04d}", collected_by=pick(techs), collected_at=when + timedelta(minutes=20),
                                                received_at=when + timedelta(minutes=50), received_by=pick(techs))
            if status in ("resulted", "verified"):
                for c in codes:
                    v, flag = _value(tests[c])
                    LabResult.objects.create(hospital=h, lab_order=o, lab_test=tests[c], value=v, unit=tests[c].unit, reference_range=tests[c].reference_range,
                                             flag=flag, entered_by=pick(techs), finalized_at=when + timedelta(hours=tests[c].tat_hours) if status == "verified" else None,
                                             finalized_by=patho.user if status == "verified" else None)
            n += 1
    return n


def radiology(h, pts, docs, users):
    procs = {}
    for name, modality, price, contrast in PROCEDURES:
        p, _ = RadiologyProcedure.objects.get_or_create(hospital=h, name=name, defaults={"modality": modality, "price": price, "uses_contrast": contrast,
                                                                                         "code": name[:3].upper(), "duration_minutes": 15 if modality in ("xray", "usg") else 30,
                                                                                         "preparation_instructions": "Fast for 6 hours." if "Abdomen" in name else ""})
        procs[name] = p
        if name in FINDINGS:
            RadiologyTemplate.objects.get_or_create(hospital=h, name=f"Normal – {name}", defaults={"modality": modality, "procedure": p, "findings": FINDINGS[name][0], "impression": FINDINGS[name][1]})
    for name, modality, room in [("Digital X-ray (DR)", "xray", "Radiology 1"), ("Ultrasound – GE Logiq", "usg", "Radiology 2"), ("128-slice CT", "ct", "CT Suite"), ("1.5T MRI", "mri", "MRI Suite")]:
        RadiologyEquipment.objects.get_or_create(hospital=h, name=name, defaults={"modality": modality, "room": room})
    radiologist = next((d for d in docs if d.department.name == "Radiology"), docs[0])
    n = 0
    for back in list(range(40, 0, -1)) + [0, 0]:
        for _ in range(RNG.randint(1, 3)):
            pr = pick(list(procs.values()))
            when = days_ago(back) if back else NOW - timedelta(hours=RNG.randint(0, 5))
            status = pick(["ordered", "scheduled", "in_progress", "completed", "reported"]) if back == 0 else "reported"
            o = RadiologyOrder.objects.create(hospital=h, patient=pick_for(pts, pr.name), procedure=pr, status=status, ordered_by=pick(docs).user,
                                              accession_number=f"RAD{timezone.localdate(when):%y%m%d}{n:04d}", priority=pick(["routine", "routine", "urgent"]),
                                              clinical_history=pick(["Cough with fever", "Knee pain on walking", "Abdominal pain right upper quadrant", "Headache with vomiting", "Breathlessness"]))
            RadiologyOrder.objects.filter(pk=o.pk).update(ordered_at=when)
            if status == "reported":
                f, imp = FINDINGS.get(pr.name, ("Study performed as per protocol. No significant abnormality detected.", "Normal study."))
                RadiologyReport.objects.create(hospital=h, radiology_order=o, findings=f, impression=imp, reported_by=radiologist.user,
                                               finalized_at=when + timedelta(hours=3), finalized_by=radiologist.user)
            n += 1
    return n


def run(ctx):
    h = ctx["h"]
    tests = catalogue(h)
    return {"lab_tests": len(tests), "lab_orders": lab_orders(h, ctx["pts"], ctx["docs"], ctx["users"], tests), "radiology_orders": radiology(h, ctx["pts"], ctx["docs"], ctx["users"])}
