"""OPD front desk: register (or find) the patient, open the visit with the
right doctor, work out new vs follow-up and the fee, bill it with any
discount, and take payment, in one transaction (NABH AAC.1, AAC.2, FPM.3).

Rules a hospital sets (apps.finance.models_frontdesk):
- ConsultationFeeRule: new-visit and follow-up fees, free follow-up window;
- FrontDeskPolicy: registration fee, discount limit, which payers may be
  billed on credit, rounding to the rupee.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_DOWN, Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.hr.staff_rate import default_category

from .models import Appointment, Doctor, OPDVisit, Slot

ZERO = Decimal("0")
PAID_VISIT_TYPES = (OPDVisit.VisitType.NEW, OPDVisit.VisitType.FOLLOW_UP)


def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


# --- new vs follow-up -------------------------------------------------------------


@dataclass
class VisitAssessment:
    visit_type: str
    reason: str
    last_paid_visit: OPDVisit | None = None
    free_follow_ups_used: int = 0


def assess_visit(patient, doctor, rule, today=None) -> VisitAssessment:
    """New, paid follow-up or free follow-up, from this patient's last paid
    visit to the same doctor and the fee rule's windows."""
    if patient is None or patient.pk is None:
        return VisitAssessment(OPDVisit.VisitType.NEW, "New patient")
    today = today or timezone.localdate()
    last = (
        OPDVisit.objects.filter(patient=patient, doctor=doctor, visit_type__in=PAID_VISIT_TYPES)
        .exclude(appointment__status=Appointment.Status.CANCELLED).order_by("-created_at").first()
    )
    if last is None:
        return VisitAssessment(OPDVisit.VisitType.NEW, "First visit to this doctor")
    days = (today - timezone.localtime(last.created_at).date()).days
    free_used = OPDVisit.objects.filter(patient=patient, doctor=doctor, visit_type=OPDVisit.VisitType.FREE_FOLLOW_UP, created_at__gt=last.created_at).count()
    free_days = rule.free_follow_up_days if rule else 0
    valid_days = rule.follow_up_valid_days if rule else 0
    max_free = rule.max_free_follow_ups if rule else 0
    if days <= free_days and free_used < max_free:
        return VisitAssessment(OPDVisit.VisitType.FREE_FOLLOW_UP, f"Free follow-up: last paid visit {days} day(s) ago", last, free_used)
    if days <= valid_days:
        why = "free follow-ups used" if days <= free_days else f"last paid visit {days} day(s) ago"
        return VisitAssessment(OPDVisit.VisitType.FOLLOW_UP, f"Paid follow-up: {why}", last, free_used)
    return VisitAssessment(OPDVisit.VisitType.NEW, f"New visit: last paid visit {days} day(s) ago", last, free_used)


# --- charges --------------------------------------------------------------------------


@dataclass
class Charge:
    description: str
    amount: Decimal
    tariff: object = None
    doctor: object = None
    source: str = ""
    source_ref: str = ""


@dataclass
class ChargeSheet:
    assessment: VisitAssessment
    charges: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((c.amount for c in self.charges), ZERO)


def build_charges(*, hospital_id, patient, doctor, rate_category, is_new_patient, package_ids=(), visit_type=None) -> ChargeSheet:
    from apps.finance.models import ConsultationFeeRule, FrontDeskPolicy
    from apps.packages.models import HealthPackage

    rule = ConsultationFeeRule.for_visit(hospital_id, doctor)
    assessment = assess_visit(None if is_new_patient else patient, doctor, rule)
    if visit_type and visit_type != assessment.visit_type:
        assessment = VisitAssessment(visit_type, f"Set by the front desk (suggested: {assessment.reason})", assessment.last_paid_visit, assessment.free_follow_ups_used)
    sheet = ChargeSheet(assessment)
    policy = FrontDeskPolicy.for_hospital(hospital_id)

    if is_new_patient and policy.registration_fee_tariff_id:
        t = policy.registration_fee_tariff
        sheet.charges.append(Charge(t.name, money(t.rate_for(rate_category)), tariff=t, source="registration"))

    if rule is None:
        sheet.warnings.append("No consultation fee is set for this doctor or department. Add one under Finance → Consultation fees.")
    elif assessment.visit_type == OPDVisit.VisitType.FREE_FOLLOW_UP:
        sheet.charges.append(Charge(f"Follow-up consultation (free) - Dr. {doctor.name}", ZERO, tariff=rule.follow_up_tariff or rule.new_visit_tariff, doctor=doctor, source="consultation"))
    else:
        t = rule.follow_up_tariff if assessment.visit_type == OPDVisit.VisitType.FOLLOW_UP and rule.follow_up_tariff_id else rule.new_visit_tariff
        label = "Follow-up consultation" if assessment.visit_type == OPDVisit.VisitType.FOLLOW_UP else "Consultation"
        sheet.charges.append(Charge(f"{label} - Dr. {doctor.name}", money(t.rate_for(rate_category)), tariff=t, doctor=doctor, source="consultation"))

    for package in HealthPackage.objects.filter(hospital_id=hospital_id, pk__in=list(package_ids or []), is_active=True):
        sheet.charges.append(Charge(f"Package: {package.name}", money(package.price), source="package", source_ref=str(package.pk)))
    return sheet


# --- walk-in appointment ---------------------------------------------------------------


def appointment_for_today(*, hospital, patient, doctor, user):
    """Today's booked appointment with this doctor if there is one,
    otherwise a walk-in appointment on a slot made for now. Checked in
    either way, which assigns the OPD queue token."""
    from .services import book_appointment, check_in

    today = timezone.localdate()
    existing = Appointment.objects.filter(patient=patient, doctor=doctor, slot__date=today, status=Appointment.Status.BOOKED).first()
    if existing is None:
        now = timezone.localtime()
        start = now.time().replace(microsecond=0)
        while Slot.objects.filter(doctor=doctor, date=today, start_time=start).exists():
            start = (datetime.combine(today, start) + timedelta(seconds=1)).time()
        end = (datetime.combine(today, start) + timedelta(minutes=doctor.default_consultation_minutes or 15)).time()
        if end <= start:
            end = time(23, 59, 59)
        slot = Slot.objects.create(hospital=hospital, doctor=doctor, date=today, start_time=start, end_time=end)
        existing = book_appointment(patient=patient, slot=slot, source=Appointment.Source.WALK_IN, reason="OPD registration", booked_by=user)
    return check_in(existing)


# --- discount -----------------------------------------------------------------------------


def authorise_discount_at_counter(*, hospital_id, approver_id, approver_password):
    """The approver confirms with their own password at the counter."""
    User = get_user_model()
    approver = User.objects.filter(pk=approver_id, hospital_id=hospital_id, is_active=True, is_blocked=False).first()
    if approver is None or not approver.check_password(str(approver_password or "")):
        raise ValidationError({"discount": "The approver's password is incorrect."})
    if not approver.has_perm("billing.authorize_discount"):
        raise ValidationError({"discount": "That person can't authorise discounts."})
    return approver


# --- registration ---------------------------------------------------------------------------


def estimated_dob(age_years, age_months=0, today=None) -> date:
    today = today or timezone.localdate()
    months = int(age_years or 0) * 12 + int(age_months or 0)
    year, month = divmod(today.year * 12 + today.month - 1 - months, 12)
    return date(year, month + 1, 1) if age_months else date(year, 7, 1)


@transaction.atomic
def bill_and_settle(*, request, patient, charges, rate_category, payer_type, billing, policy):
    """Bill `charges`, apply any discount (with approval above the limit),
    round, and settle: pay now (split payments, advance), credit, or wait
    for discount approval. Returns (bill, pending DiscountApproval or None)."""
    from apps.billing.models import Bill, BillItem, DiscountApproval, Payment, PatientAdvance
    from apps.billing.services import recalculate_bill

    user = request.user
    hospital = user.hospital

    # --- bill
    bill = Bill.objects.create(hospital=hospital, patient=patient, status=Bill.Status.DRAFT, patient_category=rate_category[:12])
    for c in charges:
        BillItem.objects.create(
            bill=bill, description=c.description[:255], quantity=1, unit_price=c.amount, tariff=c.tariff, doctor=c.doctor,
            hsn_sac=getattr(c.tariff, "hsn_sac", "") or "", gst_rate=getattr(c.tariff, "gst_rate", 0) or 0,
            service_date=timezone.localdate(), source=c.source, source_ref=c.source_ref,
        )
    recalculate_bill(bill)
    gross = bill.total_amount + bill.tax_amount

    # --- discount
    discount = money(billing.get("discount_amount"))
    if billing.get("discount_percent") not in (None, "") and not billing.get("discount_amount"):
        discount = (gross * Decimal(str(billing["discount_percent"])) / Decimal("100")).quantize(Decimal("0.01"))
    if discount < 0 or discount > gross:
        raise ValidationError({"discount": "The discount can't be negative or more than the bill."})
    awaiting_approval = None
    if discount > 0:
        percent = (discount * 100 / gross).quantize(Decimal("0.01")) if gross else Decimal("100")
        reason = str(billing.get("discount_reason", "")).strip()
        if not reason:
            raise ValidationError({"discount_reason": "Give a reason for the discount."})
        bill.discount_amount, bill.discount_reason = discount, reason[:255]
        if percent > policy.discount_limit_percent:
            approval = DiscountApproval(hospital=hospital, bill=bill, amount=discount, percent=percent, reason=reason[:255], requested_by=user)
            if billing.get("approver"):
                approval.approver = authorise_discount_at_counter(hospital_id=hospital.pk, approver_id=billing["approver"], approver_password=billing.get("approver_password"))
                approval.status, approval.decided_at, approval.approved_at_counter = DiscountApproval.Status.APPROVED, timezone.now(), True
            approval.save()
            if approval.status == DiscountApproval.Status.PENDING:
                awaiting_approval = approval

    # --- rounding
    net = gross - bill.discount_amount
    bill.round_off = (net - net.quantize(Decimal("1"), rounding=ROUND_DOWN)) if policy.round_down_to_rupee else ZERO
    bill.save(update_fields=["discount_amount", "discount_reason", "round_off"])
    recalculate_bill(bill)

    # --- settlement
    settlement = billing.get("settlement") or "receipt"
    payments = billing.get("payments") or []
    if awaiting_approval is not None:
        if payments:
            raise ValidationError({"payments": "Take payment after the discount is approved."})
    elif settlement == "credit":
        if payer_type not in (policy.credit_payer_types or []):
            raise ValidationError({"settlement": f"{OPDVisit.PayerType(payer_type).label} patients pay at the counter, not on credit."})
        bill.status = Bill.Status.UNPAID
        bill.save(update_fields=["status"])
    elif bill.net_amount > 0:
        total_paid = ZERO
        for p in payments:
            amount = money(p.get("amount"))
            method = p.get("method") or Payment.PaymentMethod.CASH
            if amount <= 0:
                continue
            if method not in Payment.PaymentMethod.values:
                raise ValidationError({"payments": f"Unknown payment mode: {method}."})
            if method == Payment.PaymentMethod.ADVANCE:
                if amount > PatientAdvance.balance(patient):
                    raise ValidationError({"payments": "That's more than the patient's advance balance."})
                PatientAdvance.objects.create(hospital=hospital, patient=patient, kind=PatientAdvance.Kind.ADJUSTMENT, amount=amount, bill=bill, recorded_by=user)
            Payment.objects.create(hospital=hospital, bill=bill, amount=amount, payment_method=method, transaction_id=str(p.get("reference", ""))[:100])
            total_paid += amount
        if total_paid != bill.net_amount:
            raise ValidationError({"payments": f"Payments total ₹{total_paid}; the bill is ₹{bill.net_amount}."})
        bill.status = Bill.Status.PAID
        bill.save(update_fields=["status"])
    else:
        bill.status = Bill.Status.PAID  # nothing to pay (free follow-up)
        bill.save(update_fields=["status"])

    return bill, awaiting_approval


@transaction.atomic
def register_opd_visit(*, request, patient, is_new_patient, data) -> dict:
    """Everything after the patient record exists: consents, visit,
    appointment, bill, discount, payments. Raises ValidationError with
    field-keyed messages the form can show."""
    from apps.core.audit import log_action
    from apps.finance.models import FrontDeskPolicy

    user = request.user
    hospital = user.hospital
    visit = data.get("visit") or {}
    billing = data.get("billing") or {}

    doctor = Doctor.objects.filter(pk=visit.get("doctor"), hospital=hospital, is_active=True).first()
    if doctor is None:
        raise ValidationError({"doctor": "Choose a doctor."})
    payer_type = visit.get("payer_type") or OPDVisit.PayerType.SELF
    if payer_type not in OPDVisit.PayerType.values:
        raise ValidationError({"payer_type": "Unknown payer type."})
    policy = FrontDeskPolicy.for_hospital(hospital.pk)
    rate_category = default_category(patient, visit.get("rate_category") or policy.default_rate_category)

    sheet = build_charges(
        hospital_id=hospital.pk, patient=patient, doctor=doctor, rate_category=rate_category, is_new_patient=is_new_patient,
        package_ids=billing.get("packages") or [], visit_type=visit.get("visit_type") or None,
    )

    appointment = appointment_for_today(hospital=hospital, patient=patient, doctor=doctor, user=user)
    prepaid = appointment.prepaid_bill if appointment.prepaid_bill_id and appointment.prepaid_bill.status == "paid" else None
    if prepaid is not None:  # booked and paid online: the consultation is already billed
        sheet.charges = [c for c in sheet.charges if c.source != "consultation"]
        sheet.warnings.append(f"Consultation already paid online (bill {prepaid.bill_number or prepaid.pk}).")

    def fk(model_path, key):
        from django.apps import apps as django_apps

        value = visit.get(key)
        if not value:
            return None
        model = django_apps.get_model(model_path)
        obj = model.objects.filter(pk=value, hospital=hospital).first()
        if obj is None:
            raise ValidationError({key: "Not found in this hospital."})
        if key in ("tpa", "corporate_client", "scheme") and getattr(obj, "is_active", True) is False:
            raise ValidationError({key: f"{obj} is inactive; choose another or reactivate it."})
        return obj

    record = OPDVisit.objects.create(
        hospital=hospital, patient=patient, doctor=doctor, department_id=visit.get("department") or doctor.department_id,
        appointment=appointment, visit_type=sheet.assessment.visit_type, case_type=visit.get("case_type") or OPDVisit.CaseType.NEW_COMPLAINT,
        is_new_patient=is_new_patient, payer_type=payer_type, rate_category=rate_category,
        corporate_client=fk("packages.CorporateClient", "corporate_client"), tpa=fk("tpa.TPACompany", "tpa"),
        insurance_policy=fk("finance.InsurancePolicy", "insurance_policy"), scheme=fk("schemes.GovtScheme", "scheme"),
        campaign=fk("packages.Campaign", "campaign"), referred_by=fk("referrals.ReferringDoctor", "referred_by"),
        referral_note=str(visit.get("referral_note", ""))[:255],
        marketing_person_id=visit.get("marketing_person") or None, registered_by=user,
    )

    if prepaid is not None and not sheet.charges:
        bill, awaiting_approval = prepaid, None
    else:
        bill, awaiting_approval = bill_and_settle(
            request=request, patient=patient, charges=sheet.charges, rate_category=rate_category,
            payer_type=payer_type, billing=billing, policy=policy,
        )

    record.bill = bill
    record.save(update_fields=["bill"])
    log_action(actor=user, action="create", instance=record, request=request,
               changes={"visit_type": record.visit_type, "doctor_id": doctor.pk, "payer_type": payer_type, "bill": bill.bill_number, "net": str(bill.net_amount)})
    return {"visit": record, "bill": bill, "assessment": sheet.assessment, "warnings": sheet.warnings, "awaiting_discount_approval": awaiting_approval}


@transaction.atomic
def register_patient_only(*, request, patient, is_new_patient, data) -> dict:
    """Registration without a visit (an appointment later, lab or pharmacy
    only). Bills the registration fee for a new patient if the hospital
    charges one; otherwise there's nothing to bill."""
    from apps.core.audit import log_action
    from apps.finance.models import FrontDeskPolicy

    policy = FrontDeskPolicy.for_hospital(request.user.hospital_id)
    rate_category = default_category(patient, (data.get("visit") or {}).get("rate_category") or policy.default_rate_category)
    charges = []
    if is_new_patient and policy.registration_fee_tariff_id:
        t = policy.registration_fee_tariff
        charges.append(Charge(t.name, money(t.rate_for(rate_category)), tariff=t, source="registration"))
    bill = awaiting = None
    if charges:
        bill, awaiting = bill_and_settle(request=request, patient=patient, charges=charges, rate_category=rate_category,
                                         payer_type=OPDVisit.PayerType.SELF, billing=data.get("billing") or {}, policy=policy)
    log_action(actor=request.user, action="create", instance=patient, request=request,
               changes={"registered_without_visit": True, "bill": bill.bill_number if bill else ""})
    return {"bill": bill, "awaiting_discount_approval": awaiting}


def registration_fee_preview(hospital_id, rate_category=""):
    """The charges for registering a new patient without a visit."""
    from apps.finance.models import FrontDeskPolicy

    policy = FrontDeskPolicy.for_hospital(hospital_id)
    if not policy.registration_fee_tariff_id:
        return []
    t = policy.registration_fee_tariff
    return [Charge(t.name, money(t.rate_for(rate_category or policy.default_rate_category)), tariff=t, source="registration")]


def check_registration_permissions(user, with_visit=True):
    needed = ("patients.add_patient", "appointments.add_appointment", "billing.add_bill") if with_visit else ("patients.add_patient",)
    missing = [p for p in needed if not user.has_perm(p)]
    if missing:
        raise PermissionDenied("Your role can't register OPD visits.")
