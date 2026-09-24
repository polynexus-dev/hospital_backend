import csv
import io
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .models import GovtScheme, SchemeCase, SchemePackage

PACKAGE_SOURCE = "scheme_package"
ADJUSTMENT_SOURCE = "scheme_adjustment"
SCHEME_SOURCES = (PACKAGE_SOURCE, ADJUSTMENT_SOURCE)

DEFAULT_SCHEMES = [
    ("pmjay", "Ayushman Bharat PM-JAY", "State Health Agency / NHA", GovtScheme.BillingMode.PACKAGE, True, 15, 0, "https://tms.pmjay.gov.in"),
    ("cghs", "CGHS", "CGHS Directorate", GovtScheme.BillingMode.RATE_LIST, True, 30, 0, ""),
    ("echs", "ECHS", "ECHS Regional Centre", GovtScheme.BillingMode.RATE_LIST, True, 30, 0, ""),
]

# Allowed moves in the claim life-cycle.
TRANSITIONS = {
    "draft": {"preauth_submitted", "discharged"},
    "preauth_submitted": {"preauth_query", "preauth_approved", "preauth_rejected"},
    "preauth_query": {"preauth_submitted", "preauth_rejected"},
    "preauth_approved": {"discharged"},
    "preauth_rejected": {"preauth_submitted"},
    "discharged": {"claim_submitted"},
    "claim_submitted": {"claim_query", "claim_approved", "claim_rejected"},
    "claim_query": {"claim_submitted", "claim_rejected"},
    "claim_approved": {"settled"},
    "claim_rejected": {"claim_submitted"},
    "settled": set(),
}


class SchemeError(Exception):
    pass


def install_default_schemes(hospital_id):
    have = set(GovtScheme.objects.filter(hospital_id=hospital_id).values_list("code", flat=True))
    made = []
    for code, name, payer, mode, preauth, days, copay, portal in DEFAULT_SCHEMES:
        if code not in have:
            GovtScheme.objects.create(hospital_id=hospital_id, code=code, name=name, payer=payer, billing_mode=mode,
                                      preauth_required=preauth, claim_submission_days=days, copay_percent=copay, claim_portal_url=portal)
            made.append(code)
    return made


def import_packages(scheme, text):
    """CSV with columns: code, name, rate [, specialty, expected_los_days,
    preauth_required, implant_included, includes]. Updates packages by code."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    missing = {"code", "name", "rate"} - {(h or "").strip().lower() for h in (reader.fieldnames or [])}
    if missing:
        raise SchemeError(f"CSV is missing column(s): {', '.join(sorted(missing))}")
    created = updated = 0
    errors = []
    yes = lambda v, default: default if v in (None, "") else str(v).strip().lower() in ("1", "true", "yes", "y")  # noqa: E731
    for n, raw in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        try:
            rate = Decimal(row["rate"].replace(",", ""))
        except InvalidOperation:
            errors.append(f"Row {n}: rate '{row['rate']}' is not a number")
            continue
        if not row["code"] or not row["name"]:
            errors.append(f"Row {n}: code and name are required")
            continue
        _, was_created = SchemePackage.objects.update_or_create(
            scheme=scheme, code=row["code"], defaults={
                "hospital_id": scheme.hospital_id, "name": row["name"], "rate": rate, "specialty": row.get("specialty", ""),
                "expected_los_days": int(row["expected_los_days"]) if row.get("expected_los_days", "").isdigit() else None,
                "preauth_required": yes(row.get("preauth_required"), True), "implant_included": yes(row.get("implant_included"), True),
                "includes": row.get("includes", ""), "is_active": True,
            })
        created += was_created
        updated += not was_created
    return {"created": created, "updated": updated, "errors": errors}


def transition(case: SchemeCase, to: str, user, note="", **fields):
    if to not in TRANSITIONS.get(case.status, set()):
        raise SchemeError(f"Cannot move a case from '{case.get_status_display()}' to '{to}'.")
    now = timezone.now()
    if to == "preauth_submitted":
        if not case.case_packages.exists() and case.scheme.billing_mode == GovtScheme.BillingMode.PACKAGE:
            raise SchemeError("Add at least one package before submitting pre-auth.")
        case.preauth_submitted_at = now
        case.preauth_amount_requested = fields.get("amount") or package_total(case)
    elif to in ("preauth_approved", "preauth_rejected"):
        case.preauth_decided_at = now
        if to == "preauth_approved":
            if not fields.get("preauth_number"):
                raise SchemeError("Pre-auth number is required.")
            case.preauth_number = fields["preauth_number"]
            case.preauth_amount_approved = Decimal(str(fields.get("amount") or case.preauth_amount_requested))
    elif to == "discharged":
        if case.scheme.preauth_required and case.status == "draft":
            raise SchemeError(f"{case.scheme.name} needs pre-auth approval before discharge.")
        discharged_on = timezone.localdate(case.admission.discharged_at) if case.admission and case.admission.discharged_at else timezone.localdate()
        case.claim_due_by = discharged_on + timedelta(days=case.scheme.claim_submission_days)
    elif to == "claim_submitted":
        missing = [d for d in case.scheme.document_checklist if not case.documents.get(d)]
        if missing and not fields.get("override_documents"):
            raise SchemeError("Documents not yet attached: " + "; ".join(missing))
        if not case.claim_amount:
            raise SchemeError("Apply the case to the bill first so the claim amount is known.")
        case.claim_submitted_at = now
        case.claim_number = fields.get("claim_number") or case.claim_number
    elif to == "claim_approved":
        amount = Decimal(str(fields.get("amount") if fields.get("amount") not in (None, "") else case.claim_amount))
        case.approved_amount = amount
        case.deduction_reason = fields.get("deduction_reason", "")
        if amount < case.claim_amount and not case.deduction_reason:
            raise SchemeError("Give the reason for the deduction.")
    elif to == "settled":
        settle(case, amount=fields.get("amount"), utr=fields.get("utr_number", ""), user=user)
    old = case.status
    case.status = to
    case.log(user, old, to, note)
    case.save()
    return case


def package_total(case):
    return sum((cp.rate * cp.quantity for cp in case.case_packages.all()), Decimal("0"))


def _bill_for(case, create=True):
    from apps.billing.bed_charges import running_bill

    if case.admission_id is None:
        raise SchemeError("Link the case to an admission first.")
    return running_bill(case.admission, create=create)


@transaction.atomic
def apply_to_bill(case: SchemeCase):
    """Re-prices the admission's running bill for the scheme and computes the
    claim amount. Idempotent — scheme lines are rebuilt on every call."""
    from apps.billing.models import BillItem
    from apps.billing.services import recalculate_bill

    scheme = case.scheme
    bill = _bill_for(case)
    if bill.patient_category != scheme.code:
        bill.patient_category = scheme.code
        bill.save(update_fields=["patient_category"])
    _reprice_tariff_lines(bill)  # every run: lines added since the last apply get scheme rates too
    BillItem.objects.filter(bill=bill, source__in=SCHEME_SOURCES).delete()

    if scheme.billing_mode == GovtScheme.BillingMode.PACKAGE:
        for cp in case.case_packages.select_related("package"):
            BillItem.objects.create(bill=bill, description=f"{scheme.code.upper()} package {cp.package.code} — {cp.package.name}",
                                    quantity=cp.quantity, unit_price=cp.rate, total_price=0, source=PACKAGE_SOURCE,
                                    source_ref=f"scheme_case:{case.pk}")
        itemised = sum((i.total_price + i.tax_amount for i in bill.items.exclude(source__in=SCHEME_SOURCES)), Decimal("0"))
        if itemised:
            BillItem.objects.create(bill=bill, description="Scheme adjustment — itemised charges included in package",
                                    quantity=1, unit_price=-itemised, total_price=0, source=ADJUSTMENT_SOURCE, source_ref=f"scheme_case:{case.pk}")
        recalculate_bill(bill)
        case.claim_amount = package_total(case)
    else:
        recalculate_bill(bill)
        case.claim_amount = (bill.net_amount * (Decimal("100") - scheme.copay_percent) / 100).quantize(Decimal("0.01"))
    case.save(update_fields=["claim_amount"])
    return bill


def _reprice_tariff_lines(bill):
    """Manual tariff lines follow the bill's new patient category (scheme rates)."""
    for item in bill.items.filter(tariff__isnull=False, source=""):
        rate = item.tariff.rate_for(bill.patient_category)
        if Decimal(str(rate)) != item.unit_price:
            item.unit_price = rate
            item.save()


def reapply_for_admission(admission):
    """Called after bed charges are re-posted, so the package adjustment stays current."""
    for case in SchemeCase.objects.filter(admission=admission).exclude(status__in=["preauth_rejected", "claim_rejected", "settled"]):
        apply_to_bill(case)


def settle(case, *, amount, utr, user):
    """Books the scheme's payment against the bill (method: insurance settlement)."""
    from apps.billing.models import Payment
    from apps.billing.services import recalculate_bill

    amount = Decimal(str(amount if amount not in (None, "") else case.approved_amount))
    if amount <= 0:
        raise SchemeError("Settlement amount must be positive.")
    if not utr:
        raise SchemeError("UTR number is required.")
    bill = _bill_for(case, create=False)
    case.settled_amount, case.settled_on, case.utr_number = amount, timezone.localdate(), utr
    if bill is not None:
        if bill.status == bill.Status.DRAFT:
            bill.status = bill.Status.UNPAID
            bill.save(update_fields=["status"])
        Payment.objects.create(hospital_id=case.hospital_id, bill=bill, amount=amount, payment_method=Payment.PaymentMethod.INSURANCE,
                               transaction_id=utr)
        recalculate_bill(bill)


def dashboard(hospital_id):
    today = timezone.localdate()
    cases = SchemeCase.objects.filter(hospital_id=hospital_id).select_related("beneficiary__scheme")
    by_status = {}
    for c in cases:
        s = by_status.setdefault(c.status, {"count": 0, "amount": Decimal("0")})
        s["count"] += 1
        s["amount"] += c.claim_amount
    overdue = [c for c in cases if c.status == "discharged" and c.claim_due_by and c.claim_due_by < today]
    settled = [c for c in cases if c.status == "settled" and c.claim_submitted_at and c.settled_on]
    decided = [c for c in cases if c.status in ("claim_approved", "claim_rejected", "settled")]
    return {
        "by_status": by_status,
        "claims_overdue": [{"id": c.pk, "beneficiary": str(c.beneficiary), "due_by": c.claim_due_by, "days_late": (today - c.claim_due_by).days} for c in overdue],
        "outstanding_amount": sum((c.claim_amount for c in cases if c.status in ("discharged", "claim_submitted", "claim_query", "claim_approved")), Decimal("0")),
        "average_days_to_settle": round(sum((c.settled_on - timezone.localdate(c.claim_submitted_at)).days for c in settled) / len(settled), 1) if settled else None,
        "claim_rejection_rate": round(100 * sum(1 for c in decided if c.status == "claim_rejected") / len(decided), 1) if decided else None,
        "deductions": sum((c.claim_amount - c.approved_amount for c in cases if c.status in ("claim_approved", "settled") and c.approved_amount < c.claim_amount), Decimal("0")),
    }
