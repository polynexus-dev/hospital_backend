"""Remaining modules: oncology, cath lab, dietary, telemedicine, queue tokens,
government schemes, ICU logs, phone menu (IVR), help articles, general
ledger, stock movements, insurance claims on bills, and a payroll run made
through the application's own API."""
import secrets
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.billing.models import Bill, InsuranceClaim
from apps.cathlab.models import CathProcedure
from apps.dietary.models import DietOrder, DietType
from apps.finance.models import Expense, Ledger
from apps.governance.models import HelpArticle
from apps.icu.models import ICUAdmission, ICUDailyProgressNote, VentilatorLog
from apps.inventory.models import Item, StockTransaction
from apps.ipd.models import Admission
from apps.oncology.models import CancerCase, ChemoCycle, ChemoProtocol
from apps.queue_mgmt.models import QueueToken, ServicePoint
from apps.schemes.models import GovtScheme, SchemeBeneficiary, SchemeCase, SchemePackage
from apps.telemedicine.models import TeleConsultation
from apps.telephony.models import IVRMenu, IVRNode

from .common import NOW, RNG, TODAY, at_today, chance, days_ago, pick, pick_for


def oncology(h, pts, docs):
    onc = next((d for d in docs if d.department.name == "General Medicine"), docs[0])
    cases = [("Breast", "C50.4", "Invasive ductal carcinoma", "T2", "N1", "M0", "IIB", {"ER": "+", "PR": "+", "HER2": "1+"}),
             ("Lung", "C34.1", "Adenocarcinoma", "T3", "N2", "M0", "IIIB", {"EGFR": "negative", "ALK": "negative", "PD-L1": "60%"}),
             ("Colon", "C18.7", "Adenocarcinoma, moderately differentiated", "T3", "N1", "M0", "IIIB", {"KRAS": "wild type", "MSI": "stable"}),
             ("Cervix", "C53.9", "Squamous cell carcinoma", "T2b", "N0", "M0", "IIB", {}),
             ("Oral cavity", "C02.1", "Squamous cell carcinoma, tongue", "T2", "N1", "M0", "III", {}),
             ("Prostate", "C61", "Acinar adenocarcinoma, Gleason 7", "T2c", "N0", "M0", "IIB", {"PSA": "14.2 ng/mL"})]
    protocols = list(ChemoProtocol.objects.filter(hospital=h)) or list(ChemoProtocol.objects.all())
    n = 0
    for site, icd, hist, t, nn, m, stage, bio in cases:
        p = pick_for(pts, site, hist)
        c = CancerCase.objects.create(hospital=h, patient=p, primary_site=site, icd10_code=icd, histology=hist, t_stage=t, n_stage=nn, m_stage=m, stage_group=stage,
                                      biomarkers=bio, ecog=RNG.randint(0, 2), treatment_intent="curative", basis_of_diagnosis="histopathology",
                                      diagnosis_date=TODAY - timedelta(days=RNG.randint(30, 200)), status=pick(["on_treatment", "on_treatment", "surveillance", "workup"]), treating_oncologist=onc.user)
        if protocols and c.status == "on_treatment":
            proto = pick(protocols)
            for k in range(1, RNG.randint(2, 5)):
                ChemoCycle.objects.create(hospital=h, case=c, protocol=proto, cycle_number=k, indication="adjuvant", planned_on=TODAY - timedelta(days=21 * (4 - k)),
                                          given_on=TODAY - timedelta(days=21 * (4 - k)) if k < 3 else None, height_cm=RNG.randint(152, 175), weight_kg=RNG.randint(52, 78),
                                          fit_for_chemo=True, status="given" if k < 3 else "planned", prescribed_by=onc.user)
        n += 1
    return n


def cathlab(h, pts, docs):
    cardio = [d for d in docs if d.department.name == "Cardiology"]
    n = 0
    for i in range(10):
        ptype, ind = pick([("cag", "Chronic stable angina – evaluation"), ("ptca", "Single-vessel disease, LAD"), ("primary_pci", "Acute anterior STEMI"), ("cag", "Positive TMT")])
        when = days_ago(RNG.randint(0, 25), hour=RNG.randint(9, 17))
        door = when - timedelta(minutes=RNG.randint(40, 80)) if ptype == "primary_pci" else None
        status = "completed" if when < NOW else "scheduled"
        CathProcedure.objects.create(hospital=h, patient=pick(pts), procedure_type=ptype, urgency="emergency" if ptype == "primary_pci" else "elective", indication=ind,
                                     operator=pick(cardio), status=status, scheduled_at=when, door_at=door, started_at=when if status == "completed" else None,
                                     device_at=when + timedelta(minutes=RNG.randint(20, 45)) if (status == "completed" and ptype != "cag") else None,
                                     ended_at=when + timedelta(minutes=RNG.randint(40, 90)) if status == "completed" else None, access_site=pick(["Right radial", "Right radial", "Right femoral"]),
                                     sheath_fr=6, weight_kg=RNG.randint(55, 90))
        n += 1
    return n


def dietary(h):
    types = list(DietType.objects.filter(hospital=h)) or list(DietType.objects.all()[:10])
    n = 0
    for adm in Admission.objects.filter(hospital=h, status="admitted")[:30]:
        if not types:
            break
        dt = pick(types)
        DietOrder.objects.create(hospital=h, patient=adm.patient, admission=adm, diet_type=dt, texture=pick(["regular", "regular", "soft", "liquid"]),
                                 preference=pick(["Vegetarian", "Vegetarian", "Non-vegetarian", "Jain"]), calories=pick([1500, 1800, 2000]), protein_g=pick([50, 60, 80]),
                                 status="active", start_date=TODAY - timedelta(days=1))
        n += 1
    return n


def telemedicine(h, pts, docs):
    n = 0
    for i in range(14):
        d = pick([x for x in docs if x.department.name in ("General Medicine", "Dermatology", "Cardiology", "Paediatrics", "Endocrinology", "Neurology")] or docs)
        when = at_today(RNG.randint(10, 19), pick([0, 30])) if i < 6 else days_ago(RNG.randint(1, 15))
        status = pick(["scheduled", "scheduled", "waiting"]) if when > NOW else pick(["completed", "completed", "no_show"])
        TeleConsultation.objects.create(hospital=h, patient=pick(pts), doctor=d, scheduled_at=when, duration_minutes=15, mode=pick(["video", "video", "audio"]),
                                        room_name=f"pnx-demo-{secrets.token_hex(4)}", status=status, reason=pick(["Follow-up review", "Report discussion", "Skin rash follow-up", "BP review"]),
                                        is_follow_up=chance(0.7), consent_given=True, consent_at=when - timedelta(minutes=5))
        n += 1
    return n


def queue(h, docs, pts):
    sps = []
    for name, kind, label, prefix in [("Registration", "registration", "Counter 1", "R"), ("Billing", "billing", "Counter 3", "B"), ("Sample collection", "lab", "Lab counter", "L"),
                                      ("Pharmacy", "pharmacy", "Pharmacy counter", "P"), ("Cardiology OPD", "consultation", "Room 104", "C")]:
        sp, _ = ServicePoint.objects.get_or_create(hospital=h, name=name, defaults={"kind": kind, "counter_label": label, "token_prefix": prefix,
                                                                                   "doctor": next((d for d in docs if d.department.name == "Cardiology"), None) if kind == "consultation" else None})
        sps.append(sp)
    n = 0
    for sp in sps:
        for k in range(1, RNG.randint(12, 22)):
            issued = at_today(8, 30) + timedelta(minutes=k * RNG.randint(6, 9))
            status = "done" if issued < NOW - timedelta(minutes=40) else pick(["waiting", "waiting", "called", "serving"])
            QueueToken.objects.create(hospital=h, service_point=sp, token_date=TODAY, number=k, patient=pick(pts[-25:]), priority=chance(0.1), status=status, issued_at=issued,
                                      called_at=issued + timedelta(minutes=8) if status != "waiting" else None, completed_at=issued + timedelta(minutes=15) if status == "done" else None)
            n += 1
    return n


def schemes(h, pts):
    pmjay, _ = GovtScheme.objects.get_or_create(hospital=h, code="pmjay", defaults={"name": "Ayushman Bharat PM-JAY", "payer": "National Health Authority / SHA Maharashtra",
                                                                                  "billing_mode": "package", "empanelment_number": "HOSP27P000000 (demo)", "preauth_required": True, "claim_submission_days": 15})
    cghs, _ = GovtScheme.objects.get_or_create(hospital=h, code="cghs", defaults={"name": "CGHS", "payer": "Central Government Health Scheme", "billing_mode": "rate_list", "preauth_required": False, "claim_submission_days": 30})
    pkgs = [SchemePackage.objects.get_or_create(hospital=h, scheme=pmjay, code=c, defaults={"name": n, "specialty": s, "rate": r, "expected_los_days": los})[0]
            for c, n, s, r, los in [("SG039A", "Laparoscopic cholecystectomy", "General Surgery", 22800, 3), ("MC011A", "PTCA – single stent", "Cardiology", 90000, 3),
                                    ("SO046A", "Total knee replacement", "Orthopaedics", 80000, 5), ("MG001A", "Acute febrile illness", "General Medicine", 1800, 3)]]
    n = 0
    for i in range(12):
        p = pick(pts)
        b = SchemeBeneficiary.objects.create(hospital=h, patient=p, scheme=pmjay if chance(0.75) else cghs, beneficiary_id=f"PMJ{RNG.randint(10**9, 10**10 - 1)}",
                                             family_id=f"FAM{RNG.randint(10**6, 10**7 - 1)}", relation=pick(["self", "spouse", "son", "daughter"]), card_valid_to=TODAY + timedelta(days=300), eligibility="eligible")
        pkg = pick(pkgs)
        st = pick(["preauth_submitted", "preauth_approved", "preauth_approved", "claim_submitted", "discharged", "preauth_query"])
        SchemeCase.objects.create(hospital=h, beneficiary=b, status=st, diagnosis=pkg.name, preauth_number=f"PA{RNG.randint(10**7, 10**8 - 1)}", preauth_submitted_at=NOW - timedelta(days=RNG.randint(1, 20)),
                                  preauth_amount_requested=pkg.rate, preauth_amount_approved=pkg.rate if "approved" in st or st in ("claim_submitted", "discharged") else 0,
                                  claim_amount=pkg.rate if st == "claim_submitted" else 0, claim_due_by=TODAY + timedelta(days=RNG.randint(2, 15)))
        n += 1
    return n


def icu_logs(h, docs, users):
    nurse = (users.get("nurse") or [None])[0]
    intensivist = next((d for d in docs if d.department.name == "Pulmonology"), docs[0])
    for ia in ICUAdmission.objects.filter(hospital=h):
        if ia.ventilator_required:
            for k in range(3):
                VentilatorLog.objects.create(hospital=h, icu_admission=ia, mode=pick(["SIMV", "PRVC", "CPAP"]), ventilator_settings={"FiO2": pick([40, 50, 60]), "PEEP": pick([5, 6, 8]), "TV": pick([400, 450])},
                                             recorded_by=nurse, recorded_at=NOW - timedelta(hours=4 * k))
        ICUDailyProgressNote.objects.create(hospital=h, icu_admission=ia, doctor=intensivist, note=pick(["Haemodynamically stable on minimal support. Plan to wean.", "Afebrile; urine output adequate. Continue current antibiotics.",
                                                                                                      "Sedation reduced; following commands. Consider step-down tomorrow."]), finalized_at=NOW, finalized_by=intensivist.user)


def ivr(h, depts):
    menu, created = IVRMenu.objects.get_or_create(hospital=h, name="Main hospital line", defaults={"dial_in_number": "+912067000000", "hold_announce_seconds": 30,
                                                                                                 "hold_info_text": "Your call is important to us. Please stay on the line.", "booking_url": "https://demo.polynexus.in/book"})
    if created:
        lang = IVRNode.objects.create(hospital=h, menu=menu, digit="", label="Welcome – choose language", action="menu", prompt="Welcome to Polynexus Demo Multispeciality Hospital. For English press 1, Marathi press 2, Hindi press 3.")
        for i, (digit, label, dept) in enumerate([("1", "Appointments", "General Medicine"), ("2", "Reports & results", "Pathology"), ("3", "Emergency & ambulance", "Emergency Medicine"),
                                                  ("4", "Billing & insurance", None), ("5", "Speak to the front desk", None)]):
            IVRNode.objects.create(hospital=h, menu=menu, parent=lang, digit=digit, label=label, action="queue" if digit != "2" else "info", department=depts.get(dept) if dept else None,
                                   queue_target="frontdesk", queue_agents=3, sort_order=i, prompt=f"Connecting you to {label.lower()}.")


def help_articles(h):
    for module, cat, title, body in [
        ("opd", "guide", "Registering a walk-in patient at the OPD desk", "Search the patient by mobile or UHID first to avoid duplicates. If new, fill name, gender, date of birth or age and mobile, choose the doctor and visit type, then Register & collect. The bill and receipt print automatically."),
        ("ipd", "guide", "Admitting a patient and allocating a bed", "Open IPD › Admit patient, choose the admitting doctor and an available bed from the bed board, record the admission diagnosis and collect the deposit. Bed charges post nightly."),
        ("billing", "faq", "Why is a bill on hold?", "A manager can put a bill on hold with a reason (for example an insurer query). While held, no charges, payments or discharge can happen. Release it from Billing › Hold / credit / category."),
        ("laboratory", "tutorial", "Collecting samples and printing barcodes", "From Diagnostics › Lab, open the order, collect the sample and print the barcode label. Results entered on the analyser flow back automatically; critical values alert the doctor."),
        ("pharmacy", "guide", "Dispensing to an inpatient", "Choose the medicine and batch (FEFO), select the patient and dispense. The charge is added to the patient's running bill at MRP; returns are credited on the same bill."),
        ("tpa", "faq", "Handling an insurer query on a pre-auth", "Open the Insurance Inbox, review the query and its reply-by date, attach the requested documents and record your reply. The pre-auth moves back to submitted automatically."),
        ("telephony", "troubleshooting", "Calls are not showing in Call Logs", "Check that the telephony provider's webhook points at this hospital and that the phone menu is active. Missed calls create callback tasks with an SLA."),
        ("governance", "policy", "Password and screen-lock policy", "Passwords need at least 10 characters with upper/lower case, a digit and a symbol, and expire every 90 days. Screens lock after 10 minutes of inactivity.")]:
        HelpArticle.objects.get_or_create(hospital=h, title=title, defaults={"module": module, "category": cat, "body": body, "tags": module})


def ledger_and_stock(h):
    for b in Bill.objects.filter(hospital=h, status__in=["paid", "partially_paid"]).order_by("-created_at")[:120]:
        Ledger.objects.create(hospital=h, entry_type="revenue", category="Patient billing", amount=b.net_amount, reference_type="bill", reference_id=b.bill_number, entry_date=b.created_at)
    for e in Expense.objects.filter(hospital=h):
        Ledger.objects.create(hospital=h, entry_type="expense", category=e.category, amount=e.amount, reference_type="expense", reference_id=str(e.pk),
                              entry_date=timezone.make_aware(timezone.datetime.combine(e.expense_date, timezone.datetime.min.time())))
    for it in Item.objects.filter(hospital=h):
        for k in range(3):
            StockTransaction.objects.create(hospital=h, item=it, transaction_type=pick(["receipt", "issue", "issue", "transfer"]), quantity=RNG.choice([10, 20, 50, 100]),
                                            reference=pick(["GRN000012", "SI000004", "Ward indent – ICU", "Transfer to OT store"]), transaction_date=NOW - timedelta(days=RNG.randint(0, 25)))
    for b in Bill.objects.filter(hospital=h, admission__isnull=False, patient__insurance_provider__gt="")[:12]:
        InsuranceClaim.objects.get_or_create(bill=b, defaults={"hospital": h, "insurance_company": b.patient.insurance_provider, "policy_number": b.patient.insurance_policy_number or "POL000000",
                                                               "claimed_amount": b.net_amount, "approved_amount": b.net_amount * Decimal("0.9") if chance(0.6) else 0,
                                                               "status": pick(["submitted", "under_review", "approved", "settled"])})


def payroll(h, users):
    """Runs last month's payroll through the real API (as the HR manager), then approves it as the finance manager (maker-checker)."""
    from rest_framework.test import APIClient

    hr_user = (users.get("hr_manager") or [None])[0]
    from apps.accounts.models import User

    fin = User.objects.filter(email="owner@demo-hospital.example").first()  # approver must differ from the preparer
    if not hr_user or not fin:
        return 0
    month = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=1)
    c = APIClient()
    c.force_authenticate(hr_user)
    out = 0
    for m in [(month - timedelta(days=1)).replace(day=1), month]:
        r = c.post("/api/v1/hr/payroll-runs/", {"month": str(m)}, format="json")
        if r.status_code == 201:
            out += 1
            a = APIClient()
            a.force_authenticate(fin)
            a.post(f"/api/v1/hr/payroll-runs/{r.data['id']}/approve/")
    return out


def run(ctx):
    h, pts, docs, users, depts = ctx["h"], ctx["pts"], ctx["docs"], ctx["users"], ctx["depts"]
    out = {"cancer_cases": oncology(h, pts, docs), "cath_procedures": cathlab(h, pts, docs), "diet_orders": dietary(h), "teleconsults": telemedicine(h, pts, docs),
           "queue_tokens": queue(h, docs, pts), "scheme_cases": schemes(h, pts)}
    icu_logs(h, docs, users)
    ivr(h, depts)
    help_articles(h)
    ledger_and_stock(h)
    out["payroll_runs"] = payroll(h, users)
    return out
