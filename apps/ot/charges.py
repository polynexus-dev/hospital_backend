"""OT tariff billing: a surgery's charges from its grade x the patient's
room category (finance.OTTariff).

The room category is the bed type the patient was in when the surgery
started (from the admission's bed history); day-care / OPD surgery has no
bed and uses the grade's any-room row. The tariff splits into surgeon,
anaesthetist, assistant and OT charges; each professional fee is a bill line
credited to that doctor, so doctor payouts pick it up. The anaesthetist and
assistant lines are charged only when one is on the case.

Posting is idempotent like bed charges: the surgery's "ot" lines are
rebuilt each time. Admitted patients are charged on the running bill;
others on a bill of their own, which re-posting reuses.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

SOURCE = "ot"
SAC = "9993"


def _ref(schedule):
    return f"ot:{schedule.pk}"


def room_category_for(schedule):
    """The bed type at the time of surgery; "" when there's no admission."""
    admission = schedule.surgery_request.admission
    if admission is None:
        return ""
    at = schedule.actual_start or schedule.scheduled_start
    from apps.ipd.models import BedAllocation

    alloc = (
        BedAllocation.objects.filter(admission=admission, allocated_at__lte=at)
        .filter(Q(released_at__isnull=True) | Q(released_at__gt=at))
        .select_related("bed")
        .order_by("-allocated_at")
        .first()
    )
    bed = alloc.bed if alloc else admission.bed
    return bed.bed_type if bed else ""


def tariff_for(hospital_id, grade, room_category):
    from apps.finance.models import OTTariff

    rows = {t.room_category: t for t in OTTariff.objects.filter(hospital_id=hospital_id, grade=grade, is_active=True)}
    return rows.get(room_category) or rows.get("")


def _anaesthetist(schedule):
    """(display name, Doctor or None) — from the schedule, else the anaesthesia record."""
    user = schedule.anaesthetist
    if user is None:
        record = getattr(schedule, "anaesthesia_record", None)
        user = record.anaesthetist if record else None
    if user is None:
        return None, None
    doctor = getattr(user, "doctor_profile", None)
    name = f"Dr. {doctor.name}" if doctor else (user.get_full_name() or user.email)
    return name, doctor


def compute_ot_charges(schedule):
    """Pure calculation — nothing is saved."""
    req = schedule.surgery_request
    grade = req.surgery_grade
    room = room_category_for(schedule)
    out = {"grade": grade.name if grade else None, "room_category": room, "tariff": None, "lines": [], "total": Decimal("0"), "problem": ""}
    if grade is None:
        out["problem"] = "Set the surgery grade on the request to price it."
        return out
    tariff = tariff_for(schedule.hospital_id, grade, room)
    if tariff is None:
        out["problem"] = f"No OT tariff for {grade.name} in {room.replace('_', '-') if room else 'any'} room. Add one under Accounts > OT tariff."
        return out
    out["tariff"] = tariff.pk
    anaes_name, anaes_doctor = _anaesthetist(schedule)
    room_label = room.replace("_", "-") if room else "day care"
    parts = [
        ("surgeon", f"Surgeon fee – {req.proposed_procedure[:120]} (Dr. {schedule.surgeon.name})", tariff.surgeon_fee, schedule.surgeon),
        ("anaesthetist", f"Anaesthetist fee ({anaes_name})", tariff.anaesthetist_fee, anaes_doctor) if anaes_name else None,
        ("assistant", f"Assistant surgeon fee (Dr. {schedule.assistant_surgeon.name})", tariff.assistant_fee, schedule.assistant_surgeon) if schedule.assistant_surgeon_id else None,
        ("ot", f"OT charges – {grade.name}, {room_label}", tariff.ot_charge, None),
    ]
    for p in parts:
        if p and p[2] > 0:
            out["lines"].append({"component": p[0], "description": p[1][:255], "amount": p[2], "doctor": p[3]})
    out["total"] = sum((l["amount"] for l in out["lines"]), Decimal("0"))
    return out


def _bill_for(schedule):
    from apps.billing.bed_charges import running_bill
    from apps.billing.models import Bill, BillItem

    req = schedule.surgery_request
    if req.admission_id:
        return running_bill(req.admission)
    item = BillItem.objects.filter(source=SOURCE, source_ref=_ref(schedule), bill__hospital_id=schedule.hospital_id).exclude(
        bill__status=Bill.Status.CANCELLED).select_related("bill").first()
    if item:
        return Bill.objects.select_for_update().get(pk=item.bill_id)
    return Bill.objects.create(hospital_id=schedule.hospital_id, patient=req.patient)


@transaction.atomic
def post_ot_charges(schedule):
    """Rebuilds the surgery's OT lines on the bill. Returns (bill, result)."""
    from apps.billing.adjustments import ensure_open
    from apps.billing.models import BillItem
    from apps.billing.services import recalculate_bill

    result = compute_ot_charges(schedule)
    if result["problem"]:
        raise ValidationError({"detail": result["problem"]})
    bill = _bill_for(schedule)
    ensure_open(bill, "post OT charges")
    day = timezone.localdate(schedule.actual_start or schedule.scheduled_start)
    BillItem.objects.filter(bill=bill, source=SOURCE, source_ref=_ref(schedule)).delete()
    for line in result["lines"]:
        BillItem.objects.create(
            bill=bill, description=line["description"], quantity=1, unit_price=line["amount"], total_price=0, hsn_sac=SAC,
            doctor=line["doctor"], service_date=day, source=SOURCE, source_ref=_ref(schedule),
        )
    recalculate_bill(bill)
    if schedule.surgery_request.admission_id:
        from apps.schemes.services import reapply_for_admission

        reapply_for_admission(schedule.surgery_request.admission)
    schedule.charges_posted_at = timezone.now()
    schedule.save(update_fields=["charges_posted_at"])
    bill.refresh_from_db()
    return bill, result


@transaction.atomic
def remove_ot_charges(schedule):
    """Takes a cancelled surgery's OT lines off its bill (if any were posted)."""
    from apps.billing.adjustments import ensure_open
    from apps.billing.models import BillItem
    from apps.billing.services import recalculate_bill

    items = list(BillItem.objects.filter(source=SOURCE, source_ref=_ref(schedule), bill__hospital_id=schedule.hospital_id).select_related("bill"))
    for bill in {i.bill for i in items}:
        ensure_open(bill, "remove the OT charges")
    BillItem.objects.filter(pk__in=[i.pk for i in items]).delete()
    for bill in {i.bill for i in items}:
        recalculate_bill(bill)
    if items and schedule.surgery_request.admission_id:
        from apps.schemes.services import reapply_for_admission

        reapply_for_admission(schedule.surgery_request.admission)
    return len(items)


def describe(result, bill=None):
    """JSON-ready form of a compute/post result."""
    return {
        "grade": result["grade"], "room_category": result["room_category"], "problem": result["problem"],
        "lines": [{"component": l["component"], "description": l["description"], "amount": l["amount"], "doctor": l["doctor"].pk if l["doctor"] else None} for l in result["lines"]],
        "total": result["total"], "bill": bill.pk if bill else None, "bill_number": bill.bill_number if bill else None,
        "bill_net_amount": bill.net_amount if bill else None,
    }
