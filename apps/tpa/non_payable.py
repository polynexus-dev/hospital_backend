"""Insurance non-payable items: which lines of an insurance / corporate bill
the insurer won't pay, so the patient pays them and the rest is claimed.

The insurer is the patient's current insurance policy's TPA, else the TPA
of their latest live pre-auth. Lists for that insurer and lists for every
insurer apply, when the bill's patient category is one the list covers.
"""
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from .models import NonPayableItem, PreAuthRequest

SKIPPED_SOURCES = ("credit_note", "scheme_package", "scheme_adjustment")
CENT = Decimal("0.01")


def payer_for(bill):
    """The TPACompany paying for this bill, or None."""
    from apps.finance.models import InsurancePolicy

    today = timezone.localdate()
    policy = (
        InsurancePolicy.objects.filter(patient_id=bill.patient_id, tpa_company__isnull=False, is_active=True)
        .exclude(eligibility_status="ineligible")
        .filter(Q(valid_from__isnull=True) | Q(valid_from__lte=today), Q(valid_to__isnull=True) | Q(valid_to__gte=today))
        .select_related("tpa_company").order_by("-id").first()
    )
    if policy is not None:
        return policy.tpa_company
    pre = PreAuthRequest.objects.filter(patient_id=bill.patient_id).exclude(status=PreAuthRequest.Status.REJECTED).select_related("tpa_company").order_by("-submitted_at").first()
    return pre.tpa_company if pre else None


def rules_for(bill, payer=None):
    payer = payer if payer is not None else payer_for(bill)
    rules = NonPayableItem.objects.filter(
        hospital_id=bill.hospital_id, non_payable_list__is_active=True,
    ).filter(Q(non_payable_list__tpa_company__isnull=True) | Q(non_payable_list__tpa_company=payer)).select_related("non_payable_list", "tariff", "medicine")
    return [r for r in rules if bill.patient_category in r.non_payable_list.categories()]


def _match(item, rules):
    desc = (item.description or "").lower()
    for r in rules:
        if r.tariff_id and r.tariff_id == item.tariff_id:
            return r
        if r.medicine_id and r.medicine.name.lower() in desc:
            return r
        if r.keyword and r.keyword.lower() in desc:
            return r
    return None


def flag_bill(bill, items=None):
    """Marks the bill's non-payable lines and returns the patient-payable
    amount (those lines with their GST). Lines are saved only if their flag
    changes. Bills outside every list's categories are cleared."""
    from apps.billing.models import BillItem

    items = list(items if items is not None else bill.items.all())
    rules = rules_for(bill) if _any_list_covers(bill) else []
    total = Decimal("0")
    for i in items:
        rule = None if (i.source in SKIPPED_SOURCES or i.total_price <= 0) else _match(i, rules)
        flag, reason = rule is not None, (f"{rule.non_payable_list.name}: {rule.label()}"[:255] if rule else "")
        if (i.non_payable, i.non_payable_reason) != (flag, reason):
            i.non_payable, i.non_payable_reason = flag, reason
            BillItem.objects.filter(pk=i.pk).update(non_payable=flag, non_payable_reason=reason)
        if flag:
            total += Decimal(i.total_price) + Decimal(i.tax_amount)
    return total.quantize(CENT)


def _any_list_covers(bill):
    from .models import NonPayableList

    return any(bill.patient_category in l.categories() for l in NonPayableList.objects.filter(hospital_id=bill.hospital_id, is_active=True))


def split(bill):
    """For the bill screen / claim: payer-payable vs patient-payable, line by line."""
    payer = payer_for(bill)
    lines = [{"id": i.pk, "description": i.description, "amount": (Decimal(i.total_price) + Decimal(i.tax_amount)).quantize(CENT),
              "non_payable": i.non_payable, "reason": i.non_payable_reason} for i in bill.items.all()]
    patient = Decimal(bill.non_payable_amount or 0)
    return {
        "bill": bill.pk, "patient_category": bill.patient_category, "payer": payer.name if payer else None,
        "gross": (Decimal(bill.total_amount) + Decimal(bill.tax_amount)).quantize(CENT),
        "patient_payable": patient, "payer_payable": max(Decimal(bill.net_amount) - patient, Decimal("0")).quantize(CENT),
        "lines": lines,
    }


OPEN_STATUSES = ("draft", "unpaid", "partially_paid")


def recheck_open_bills(hospital_id):
    """After a non-payable list or item changes: re-flag every open bill
    (draft / unpaid / part-paid). Paid and cancelled bills keep the split
    they were settled on. Returns how many bills' patient share changed."""
    from apps.billing.models import Bill

    changed = 0
    for bill in Bill.objects.filter(hospital_id=hospital_id, status__in=OPEN_STATUSES).prefetch_related("items"):
        amount = flag_bill(bill, list(bill.items.all()))
        if amount != bill.non_payable_amount:
            Bill.objects.filter(pk=bill.pk).update(non_payable_amount=amount)
            changed += 1
    return changed
