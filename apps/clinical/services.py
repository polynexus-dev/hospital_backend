"""
Clinical decision support (NABH COP.12.a/b, MOM.2.c/g, COP.1.k/m,
COP.12.c). Pure functions return alert dicts; `persist_alerts` turns them
into ClinicalAlert rows (the clinician's inbox + the audit of what was
shown). Matching is deliberately simple and explainable — lower-cased
substring matching on generic names plus a small drug-class map — rather
than an opaque model, because a clinician has to be able to see *why* an
alert fired.
"""
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.utils import timezone

from .models import Allergy, ClinicalAlert, DrugConditionRule, DrugInteraction, NotifiableDisease, NotifiableDiseaseReport

# Allergy to a class name also flags every member we know of.
DRUG_CLASSES = {
    "penicillin": ["penicillin", "amoxicillin", "ampicillin", "piperacillin", "cloxacillin", "flucloxacillin", "benzathine"],
    "cephalosporin": ["cef", "ceph"],
    "sulfa": ["sulfamethoxazole", "sulfasalazine", "cotrimoxazole", "sulfadiazine"],
    "nsaid": ["ibuprofen", "diclofenac", "naproxen", "aspirin", "ketorolac", "mefenamic", "aceclofenac", "etoricoxib"],
    "quinolone": ["ciprofloxacin", "levofloxacin", "ofloxacin", "moxifloxacin", "norfloxacin"],
    "macrolide": ["azithromycin", "clarithromycin", "erythromycin"],
    "opioid": ["morphine", "tramadol", "fentanyl", "codeine", "pethidine", "tapentadol"],
    "statin": ["atorvastatin", "rosuvastatin", "simvastatin", "pravastatin"],
    "iodine": ["iohexol", "iopamidol", "iodinated contrast", "contrast"],
}


def _norm(text):
    return (text or "").strip().lower()


def med_name(item):
    if isinstance(item, dict):
        return _norm(item.get("generic_name") or item.get("name") or item.get("medicine"))
    return _norm(str(item))


def _base(name):
    """Drug base name without strength/form: 'warfarin 5mg tab' -> 'warfarin'."""
    import re

    m = re.match(r"[a-z][a-z\-]+", _norm(name))
    return m.group(0) if m else _norm(name)


def _members(term):
    term = _norm(term)
    return DRUG_CLASSES.get(term, [term])


def _matches(drug, term):
    drug = _norm(drug)
    return any(m and m in drug for m in _members(term))


def active_medications(patient, *, days=30, exclude_prescription_id=None):
    """Medications the patient is currently on: e-prescriptions in the last
    `days`, plus anything given on the MAR in the last 48h."""
    from apps.patients.models import Prescription

    names = []
    since = timezone.now() - timedelta(days=days)
    qs = Prescription.objects.filter(patient=patient, created_at__gte=since)
    if exclude_prescription_id:
        qs = qs.exclude(pk=exclude_prescription_id)
    for rx in qs:
        for item in rx.medications or []:
            n = med_name(item)
            if n:
                names.append(n)
    try:
        from apps.nursing.models import MedicationAdministration

        recent = MedicationAdministration.objects.filter(
            hospital_id=patient.hospital_id, administered_at__gte=timezone.now() - timedelta(hours=48), patient=patient,
        ).values_list("medication_name", flat=True)
        names.extend(_norm(n) for n in recent)
    except Exception:  # MAR may not carry a direct patient FK in older rows
        pass
    return sorted(set(names))


def patient_conditions(patient):
    """Free-text + ICD codes the rule engine matches against."""
    from apps.opd.models import Diagnosis

    texts = []
    for d in Diagnosis.objects.filter(encounter__patient=patient).values("icd_code", "description"):
        texts.append(_norm(d["icd_code"]))
        texts.append(_norm(d["description"]))
    for a in patient.assessments.all().values_list("provisional_diagnosis", flat=True):
        texts.append(_norm(a))
    flags = set()
    if patient.date_of_birth:
        age = (timezone.localdate() - patient.date_of_birth).days // 365
        if age > 65:
            flags.add("age>65")
        if age < 12:
            flags.add("child")
        if age < 18:
            flags.add("minor")
    if patient.assessments.filter(category__in=["antenatal", "obstetrics"]).exists():
        flags.add("pregnant")
    return [t for t in texts if t], flags


def _condition_hit(keywords, texts, flags):
    for kw in keywords:
        k = _norm(kw)
        if not k:
            continue
        if k in flags:
            return kw
        if any(t.startswith(k) or k in t for t in texts):
            return kw
    return None


def check_medications(patient, medications, *, exclude_prescription_id=None):
    """Returns a list of alert dicts for a proposed list of medications."""
    alerts = []
    proposed = [n for n in (med_name(m) for m in medications) if n]
    if not proposed:
        return alerts

    # 1. Allergies / ADRs
    for allergy in Allergy.objects.filter(patient=patient, status=Allergy.Status.ACTIVE, allergen_type=Allergy.AllergenType.DRUG):
        for drug in proposed:
            if _matches(drug, allergy.allergen):
                alerts.append({
                    "alert_type": ClinicalAlert.AlertType.ALLERGY,
                    "severity": ClinicalAlert.Severity.CRITICAL if allergy.severity == Allergy.Severity.SEVERE else ClinicalAlert.Severity.WARNING,
                    "title": f"Allergy: {drug} vs recorded allergy to {allergy.allergen}",
                    "message": f"Patient has a recorded {allergy.get_severity_display().lower()} {'adverse reaction' if allergy.is_adverse_drug_reaction else 'allergy'} to {allergy.allergen}"
                               + (f" ({allergy.reaction})" if allergy.reaction else "") + ".",
                })

    current = active_medications(patient, exclude_prescription_id=exclude_prescription_id)
    everything = proposed + [c for c in current if c not in proposed]

    # 2. Interactions (proposed×proposed and proposed×current)
    rules = list(DrugInteraction.objects.filter(hospital_id=patient.hospital_id))
    seen = set()
    for i, a in enumerate(proposed):
        for b in everything:
            if a == b:
                continue
            for r in rules:
                if (_matches(a, r.drug_a) and _matches(b, r.drug_b)) or (_matches(a, r.drug_b) and _matches(b, r.drug_a)):
                    key = (r.pk, frozenset((a, b)))
                    if key in seen:
                        continue
                    seen.add(key)
                    alerts.append({
                        "alert_type": ClinicalAlert.AlertType.INTERACTION,
                        "severity": ClinicalAlert.Severity.CRITICAL if r.severity in ("major", "contraindicated") else ClinicalAlert.Severity.WARNING,
                        "title": f"{r.get_severity_display()} interaction: {a} + {b}",
                        "message": r.description + (f" Recommendation: {r.recommendation}" if r.recommendation else ""),
                    })

    # 3. Therapeutic duplication with what the patient is already on
    for drug in proposed:
        for c in current:
            if _base(drug) == _base(c) or (len(drug) > 4 and (drug in c or c in drug)):
                alerts.append({
                    "alert_type": ClinicalAlert.AlertType.DUPLICATE_ORDER,
                    "severity": ClinicalAlert.Severity.WARNING,
                    "title": f"Duplicate medication: {drug}",
                    "message": f"The patient already has an active order for {c}.",
                })
                break

    # 4. Drug–condition contraindications
    texts, flags = patient_conditions(patient)
    for rule in DrugConditionRule.objects.filter(hospital_id=patient.hospital_id, applies_to="medication"):
        for drug in proposed:
            if _matches(drug, rule.drug):
                hit = _condition_hit(rule.condition_keywords, texts, flags)
                if hit:
                    alerts.append({
                        "alert_type": ClinicalAlert.AlertType.CONTRAINDICATION,
                        "severity": ClinicalAlert.Severity.CRITICAL if rule.severity in ("major", "contraindicated") else ClinicalAlert.Severity.WARNING,
                        "title": f"{drug}: caution with {hit}",
                        "message": rule.message,
                    })

    # 5. High-risk / LASA / restricted antimicrobials (pharmacy formulary flags)
    try:
        from apps.pharmacy.models import Medicine

        for med in Medicine.objects.filter(hospital_id=patient.hospital_id, is_active=True):
            names = {_norm(med.name), _norm(med.generic_name)} - {""}
            for drug in proposed:
                if not any(n and (n in drug or drug in n) for n in names):
                    continue
                if getattr(med, "is_high_risk", False):
                    alerts.append({
                        "alert_type": ClinicalAlert.AlertType.HIGH_RISK_MEDICATION,
                        "severity": ClinicalAlert.Severity.WARNING,
                        "title": f"High-risk medication: {med.name}",
                        "message": "High-alert medication — double-check dose, route and patient; independent verification required at dispensing.",
                    })
                if getattr(med, "is_lasa", False):
                    alerts.append({
                        "alert_type": ClinicalAlert.AlertType.HIGH_RISK_MEDICATION,
                        "severity": ClinicalAlert.Severity.INFO,
                        "title": f"Look-alike/sound-alike: {med.name}",
                        "message": f"{med.name} is on the LASA list" + (f" (confused with {med.lasa_pair})" if getattr(med, "lasa_pair", "") else "") + ". Confirm the intended drug.",
                    })
                if getattr(med, "is_restricted_antimicrobial", False):
                    alerts.append({
                        "alert_type": ClinicalAlert.AlertType.CDSS,
                        "severity": ClinicalAlert.Severity.WARNING,
                        "title": f"Restricted antimicrobial: {med.name}",
                        "message": "Per the hospital antimicrobial policy this drug needs an approval (indication + culture) before dispensing.",
                    })
    except ImportError:
        pass
    return alerts


def check_radiology_contraindications(patient, modality, procedure_name=""):
    """AAC.4.l — e.g. CT with contrast in a patient with iodine allergy or
    renal failure, X-ray/CT in pregnancy, MRI with a pacemaker."""
    alerts = []
    texts, flags = patient_conditions(patient)
    subject = f"{_norm(modality)} {_norm(procedure_name)}"
    for rule in DrugConditionRule.objects.filter(hospital_id=patient.hospital_id, applies_to="radiology"):
        if _norm(rule.drug) in subject:
            hit = _condition_hit(rule.condition_keywords, texts, flags)
            if hit:
                alerts.append({
                    "alert_type": ClinicalAlert.AlertType.CONTRAINDICATION,
                    "severity": ClinicalAlert.Severity.CRITICAL if rule.severity in ("major", "contraindicated") else ClinicalAlert.Severity.WARNING,
                    "title": f"{procedure_name or modality}: contraindicated with {hit}",
                    "message": rule.message,
                })
    if "contrast" in subject:
        for allergy in Allergy.objects.filter(patient=patient, status=Allergy.Status.ACTIVE):
            if _matches("iodinated contrast", allergy.allergen) or "contrast" in _norm(allergy.allergen) or "iodine" in _norm(allergy.allergen):
                alerts.append({
                    "alert_type": ClinicalAlert.AlertType.ALLERGY,
                    "severity": ClinicalAlert.Severity.CRITICAL,
                    "title": "Contrast allergy",
                    "message": f"Patient has a recorded allergy to {allergy.allergen}.",
                })
    return alerts


def persist_alerts(patient, alerts, *, source=None, target_user=None, target_department=""):
    rows = []
    ct = ContentType.objects.get_for_model(source) if source is not None else None
    for a in alerts:
        rows.append(ClinicalAlert.objects.create(
            hospital_id=patient.hospital_id if patient is not None else getattr(source, "hospital_id", None),
            patient=patient,
            alert_type=a["alert_type"],
            severity=a["severity"],
            title=a["title"][:200],
            message=a["message"],
            target_user=target_user,
            target_department=target_department,
            content_type=ct,
            object_id=str(source.pk) if source is not None else "",
        ))
    return rows


# --- Critical results (COP.1.m) -------------------------------------------


def raise_critical_result_alert(*, patient, test_name, value, unit, flag, source, ordering_user=None):
    """Alerts the ordering clinician directly plus the ward/lab so the
    result isn't missed if that clinician is off shift."""
    title = f"CRITICAL {test_name}: {value} {unit}".strip()
    message = f"Critical ({flag}) result for {patient.full_name} ({patient.uhid}) — {test_name} = {value} {unit}. Act and acknowledge."
    alerts = [{"alert_type": ClinicalAlert.AlertType.CRITICAL_RESULT, "severity": ClinicalAlert.Severity.CRITICAL, "title": title, "message": message}]
    rows = persist_alerts(patient, alerts, source=source, target_user=ordering_user)
    rows += persist_alerts(patient, alerts, source=source, target_department="nursing")
    rows += persist_alerts(patient, alerts, source=source, target_department="laboratory")
    return rows


# --- Duplicate orders (COP.1.k) -------------------------------------------


def duplicate_lab_orders(patient, test_ids, *, hours=24):
    from apps.laboratory.models import LabOrder

    since = timezone.now() - timedelta(hours=hours)
    dupes = []
    for order in LabOrder.objects.filter(patient=patient, ordered_at__gte=since).exclude(status__in=["cancelled", "rejected"]).prefetch_related("ordered_tests"):
        for t in order.ordered_tests.all():
            if t.pk in test_ids:
                dupes.append({"order_id": order.pk, "test_id": t.pk, "test": t.name, "ordered_at": order.ordered_at})
    return dupes


def duplicate_radiology_orders(patient, procedure_id, *, hours=24):
    from apps.radiology.models import RadiologyOrder

    since = timezone.now() - timedelta(hours=hours)
    return list(
        RadiologyOrder.objects.filter(patient=patient, procedure_id=procedure_id, ordered_at__gte=since)
        .exclude(status__in=["cancelled"]).values("id", "ordered_at", "status")
    )


# --- Notifiable diseases (COP.12.c) ----------------------------------------


def check_notifiable(patient, diagnosis_text, icd_code=""):
    text, code = _norm(diagnosis_text), _norm(icd_code)
    created = []
    for disease in NotifiableDisease.objects.filter(hospital_id=patient.hospital_id, is_active=True):
        hit = any(code and code.startswith(_norm(c)) for c in disease.icd_codes) or any(_norm(k) and _norm(k) in text for k in disease.keywords)
        if not hit:
            continue
        if NotifiableDiseaseReport.objects.filter(patient=patient, disease=disease, detected_at__gte=timezone.now() - timedelta(days=30)).exists():
            continue
        report = NotifiableDiseaseReport.objects.create(
            hospital_id=patient.hospital_id, patient=patient, disease=disease,
            diagnosis_text=diagnosis_text[:255], icd_code=icd_code[:16],
            due_by=timezone.now() + timedelta(hours=disease.report_within_hours),
        )
        persist_alerts(patient, [{
            "alert_type": ClinicalAlert.AlertType.NOTIFIABLE_DISEASE,
            "severity": ClinicalAlert.Severity.CRITICAL,
            "title": f"Notifiable disease: {disease.name}",
            "message": f"{disease.name} must be reported to {disease.authority} within {disease.report_within_hours} hours.",
        }], source=report, target_department="medical_records")
        created.append(report)
    return created


def open_alerts_for(user):
    return ClinicalAlert.objects.filter(hospital_id=user.hospital_id, acknowledged_at__isnull=True).filter(
        Q(target_user=user) | Q(target_user__isnull=True)
    )
