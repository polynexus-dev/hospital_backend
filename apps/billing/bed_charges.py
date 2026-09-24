"""Automatic bed / room-rent billing for inpatients.

Bed-days come from the admission's BedAllocation history (so transfers are
already accounted for), counted per the hospital's BedBillingPolicy:

* 24-hour cycle — day 1 starts at admission; a new day is charged once a
  stay runs `grace_hours` into the next 24 hours.
* Calendar day — every calendar date the patient is in a bed; the discharge
  date only if they leave after `checkout_hour`.

On a day with a transfer, the bed charged is the higher-rate one (or the one
occupied longest, per policy). Each bed-day attracts every tariff of the
most specific matching BedChargeRule level (ward → bed type → catch-all).

Posting is idempotent: the bill's "bed_charge" lines are rebuilt from scratch
each run, so it can run nightly, at discharge and on demand without ever
double-charging.
"""
import logging
import math
from collections import OrderedDict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import BedBillingPolicy, BedChargeRule, Bill, BillItem

logger = logging.getLogger(__name__)
SOURCE = "bed_charge"


def policy_for(hospital_id) -> BedBillingPolicy:
    return BedBillingPolicy.objects.filter(hospital_id=hospital_id).first() or BedBillingPolicy(hospital_id=hospital_id)


def _windows(start, end, policy):
    """[(label_date, window_start, window_end)] — one per billable bed-day."""
    if policy.cycle == BedBillingPolicy.Cycle.CALENDAR_DAY:
        tz = timezone.get_current_timezone()
        first, last = timezone.localtime(start).date(), timezone.localtime(end).date()
        if last > first and timezone.localtime(end).time() < time(policy.checkout_hour):
            last -= timedelta(days=1)
        out, day = [], first
        while day <= last:
            day_start = timezone.make_aware(datetime.combine(day, time.min), tz)
            out.append((day, max(start, day_start), min(end, day_start + timedelta(days=1))))
            day += timedelta(days=1)
        return out or [(first, start, end)]
    hours = (end - start).total_seconds() / 3600
    n = max(1, math.ceil((hours - policy.grace_hours) / 24))
    return [
        (timezone.localtime(start + timedelta(days=i)).date(), start + timedelta(days=i), min(start + timedelta(days=i + 1), end) if i < n - 1 else end)
        for i in range(n)
    ]


def _rules_for(bed, rules):
    ward_level = [r for r in rules if r.ward_id and r.ward_id == bed.room.ward_id and (not r.bed_type or r.bed_type == bed.bed_type)]
    if ward_level:
        return ward_level
    type_level = [r for r in rules if not r.ward_id and r.bed_type == bed.bed_type]
    return type_level or [r for r in rules if not r.ward_id and not r.bed_type]


def compute_bed_charges(admission, *, category="general", until=None):
    """Pure calculation — nothing is saved. Returns the per-day breakdown,
    the bill lines it would produce, and beds that have no charge rule."""
    from apps.ipd.models import BedAllocation

    policy = policy_for(admission.hospital_id)
    start = admission.admitted_at
    end = admission.discharged_at or until or timezone.now()
    end = max(end, start)
    allocations = list(
        BedAllocation.objects.filter(admission=admission).select_related("bed__room__ward").order_by("allocated_at")
    )
    rules = list(BedChargeRule.objects.filter(hospital_id=admission.hospital_id, is_active=True, tariff__is_active=True).select_related("tariff"))

    def components(bed):
        return [(r, Decimal(str(r.tariff.rate_for(category)))) for r in _rules_for(bed, rules)]

    days, lines, unpriced = [], OrderedDict(), set()
    for label, w_start, w_end in _windows(start, end, policy):
        overlapping = [
            (a, min(w_end, a.released_at or end) - max(w_start, a.allocated_at))
            for a in allocations
            if a.allocated_at < w_end and (a.released_at or end) > w_start
        ]
        if overlapping:
            if policy.transfer_day_rule == BedBillingPolicy.TransferRule.LONGEST:
                bed = max(overlapping, key=lambda x: x[1])[0].bed
            else:
                bed = max(overlapping, key=lambda x: (sum(rate for _, rate in components(x[0].bed)), x[1]))[0].bed
        else:
            bed = admission.bed  # no allocation history (legacy rows)
        comps = components(bed)
        if not comps:
            unpriced.add(str(bed))
        days.append({"date": label, "bed": str(bed), "bed_type": bed.bed_type, "ward": bed.room.ward.name,
                     "amount": float(sum((rate for _, rate in comps), Decimal("0")))})
        for rule, rate in comps:
            key = (rule.tariff_id, bed.pk, rate)
            line = lines.setdefault(key, {
                "tariff": rule.tariff, "bed": bed, "unit_price": rate, "days": 0, "first_date": label,
                "description": f"{rule.tariff.name} – {bed.room.ward.name}, bed {bed.bed_number}",
            })
            line["days"] += 1
    return {"policy": policy, "days": days, "lines": list(lines.values()), "unpriced_beds": sorted(unpriced)}


def running_bill(admission, *, create=True):
    bill = (
        Bill.objects.select_for_update()
        .filter(admission=admission, is_interim=False)
        .exclude(status=Bill.Status.CANCELLED)
        .order_by("created_at")
        .first()
    )
    if bill is None and create:
        bill = Bill.objects.create(hospital_id=admission.hospital_id, patient=admission.patient, admission=admission)
    return bill


@transaction.atomic
def post_bed_charges(admission, *, until=None):
    """Rebuilds the running bill's bed-charge lines. Returns (bill, result)."""
    from .services import recalculate_bill

    bill = running_bill(admission)
    result = compute_bed_charges(admission, category=bill.patient_category, until=until)
    BillItem.objects.filter(bill=bill, source=SOURCE).delete()
    for line in result["lines"]:
        t = line["tariff"]
        BillItem.objects.create(
            bill=bill, description=line["description"], quantity=line["days"], unit_price=line["unit_price"], total_price=0,
            tariff=t, hsn_sac=t.hsn_sac, gst_rate=t.gst_rate, source=SOURCE, source_ref=f"admission:{admission.pk}",
            service_date=line["first_date"],
        )
    recalculate_bill(bill)
    # A scheme (e.g. PM-JAY package) adjustment depends on the itemised
    # total, so it's rebuilt whenever the bed lines change.
    from apps.schemes.services import reapply_for_admission

    reapply_for_admission(admission)
    bill.refresh_from_db()
    return bill, result


def post_all_open_admissions(hospital_id=None):
    """Nightly: bring every current inpatient's running bill up to date."""
    from apps.ipd.models import Admission

    qs = Admission.objects.filter(status=Admission.Status.ADMITTED)
    if hospital_id:
        qs = qs.filter(hospital_id=hospital_id)
    posted = 0
    for admission in qs.select_related("bed__room__ward"):
        if not policy_for(admission.hospital_id).auto_post:
            continue
        try:
            post_bed_charges(admission)
            posted += 1
        except Exception:  # one bad admission must not stop the run
            logger.exception("Bed-charge posting failed for admission %s", admission.pk)
    return posted
