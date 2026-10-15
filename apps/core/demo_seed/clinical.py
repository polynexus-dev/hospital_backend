"""Clinical safety data for current inpatients — risk scores (through the
application's own scoring) and the alert inbox — plus realistic payment
times (each payment at its bill's time rather than the seed time)."""
from datetime import timedelta

from apps.billing.models import Payment
from apps.clinical import scoring
from apps.clinical.models import ClinicalAlert, RiskAssessment
from apps.ipd.models import Admission

from .common import NOW, RNG, pick

ALERTS = [
    ("critical_result", "critical", "Critical potassium 6.2 mmol/L", "Serum potassium 6.2 mmol/L (critical high). Repeat sample and inform the treating doctor.", "Laboratory"),
    ("critical_result", "critical", "Haemoglobin 6.8 g/dL", "Haemoglobin 6.8 g/dL (critical low). Consider transfusion; cross-match requested.", "Laboratory"),
    ("allergy", "critical", "Penicillin allergy — amoxicillin-clavulanate prescribed", "Patient has a recorded penicillin allergy (urticarial rash). Review the prescription.", "Pharmacy"),
    ("interaction", "warning", "Clopidogrel + omeprazole interaction", "Omeprazole may reduce the antiplatelet effect of clopidogrel. Consider pantoprazole.", "Pharmacy"),
    ("early_warning", "critical", "NEWS2 score 7 — urgent review", "Respiratory rate 26, SpO₂ 91 % on air, pulse 118. Escalate to the rapid response team.", "Nursing"),
    ("risk", "warning", "High fall risk (Morse 55)", "Morse Fall Scale 55. Bed rails up, call bell in reach, non-slip footwear.", "Nursing"),
    ("high_risk_medication", "warning", "Insulin infusion started", "High-alert medication: insulin infusion. Hourly capillary glucose; double-check every rate change.", "Nursing"),
    ("notifiable_disease", "info", "Dengue NS1 positive — notifiable", "Dengue NS1 antigen positive. Notify the municipal health office within 24 hours.", "Infection control"),
    ("duplicate_order", "info", "Duplicate CBC order", "A CBC was already ordered today for this patient. Cancel the duplicate if not needed.", "Laboratory"),
]

MORSE = [  # answers that give high / moderate / low risk
    {"history_of_falling": True, "secondary_diagnosis": True, "ambulatory_aid": "crutches_cane_walker", "iv_or_heparin_lock": True, "gait": "weak"},
    {"secondary_diagnosis": True, "iv_or_heparin_lock": True, "gait": "weak"},
    {"iv_or_heparin_lock": True},
]


def run(ctx):
    h = ctx["h"]
    adms = list(Admission.objects.filter(hospital=h, status="admitted").select_related("patient", "admitting_doctor__user"))
    RNG.shuffle(adms)
    n_risk = 0
    for adm in adms[:24]:
        answers = pick(MORSE)
        score, level = scoring.score("morse_fall", answers)
        # score / level are worked out by the application's scoring module (the serializer does the same)
        RiskAssessment.objects.create(hospital=h, patient=adm.patient, admission=adm, tool="morse_fall", answers=answers, score=score, risk_level=level,
                                      assessed_by=adm.admitting_doctor.user if adm.admitting_doctor else None,
                                      assessed_at=NOW - timedelta(hours=RNG.randint(1, 30)))
        n_risk += 1
    n_alert = 0
    for (atype, sev, title, msg, dept), adm in zip(ALERTS, adms[24:]):
        a = ClinicalAlert.objects.create(hospital=h, patient=adm.patient, alert_type=atype, severity=sev, title=title, message=msg, target_department=dept)
        ClinicalAlert.objects.filter(pk=a.pk).update(created_at=NOW - timedelta(minutes=RNG.randint(10, 600)))
        n_alert += 1
    # payments at the time of their bill (+ a few minutes), not at seed time
    for p in Payment.objects.filter(hospital=h).select_related("bill"):
        Payment.objects.filter(pk=p.pk).update(paid_at=p.bill.created_at + timedelta(minutes=RNG.randint(2, 40)))
    return f"{n_risk} risk scores, {n_alert} clinical alerts, payments re-timed"


def fix_existing(h):
    """Corrections for a database seeded before erp.py marked expired units and spread ambulance trips."""
    from apps.bloodbank.models import BloodUnit
    from apps.support_services.models import Ambulance, AmbulanceTrip
    from .common import TODAY
    from datetime import timedelta as td
    from .common import RNG as R
    life = {"whole_blood": 35, "prbc": 42, "ffp": 365, "platelets": 5}
    for u in BloodUnit.objects.filter(hospital=h, expiry_date__lt=TODAY):
        if R.random() < 0.9:  # re-date to stock within its shelf life
            coll = TODAY - td(days=R.randint(0, min(30, life.get(u.component, 35) - 1)))
            BloodUnit.objects.filter(pk=u.pk).update(collection_date=coll, expiry_date=coll + td(days=life.get(u.component, 35)),
                                                     status=R.choice(["available"] * 6 + ["reserved", "issued"]))
        else:
            BloodUnit.objects.filter(pk=u.pk).update(status="discarded")
    fleet = list(Ambulance.objects.filter(hospital=h))
    for i, t in enumerate(AmbulanceTrip.objects.filter(hospital=h).order_by("pk")):
        AmbulanceTrip.objects.filter(pk=t.pk).update(ambulance=fleet[i % len(fleet)])
