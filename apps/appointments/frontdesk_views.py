"""OPD front-desk API (mounted at /api/v1/frontdesk/)."""
from decimal import Decimal

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.crud import TenantCRUDViewSet, model_serializer
from apps.core.viewsets import TenantScopedViewSetMixin
from apps.patients.models import Patient

from . import frontdesk_print
from .frontdesk import (
    build_charges, check_registration_permissions, estimated_dob, money, register_opd_visit, register_patient_only, registration_fee_preview,
)
from .models import Doctor, OPDVisit


def _patient(request, pk):
    patient = Patient.objects.filter(pk=pk, hospital_id=request.user.hospital_id).first()
    if patient is None:
        raise ValidationError({"patient": "Patient not found."})
    return patient


def _pdf(content, filename):
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


class OPDVisitSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    uhid = serializers.CharField(source="patient.uhid", read_only=True)
    doctor_name = serializers.CharField(source="doctor.name", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)
    token = serializers.IntegerField(source="appointment.queue_token", read_only=True)
    visit_status = serializers.CharField(source="appointment.status", read_only=True)
    bill_number = serializers.CharField(source="bill.bill_number", read_only=True, default=None)
    net_amount = serializers.DecimalField(source="bill.net_amount", max_digits=12, decimal_places=2, read_only=True, default=None)
    bill_status = serializers.CharField(source="bill.status", read_only=True, default=None)
    visit_type_label = serializers.CharField(source="get_visit_type_display", read_only=True)
    payer_type_label = serializers.CharField(source="get_payer_type_display", read_only=True)

    class Meta:
        model = OPDVisit
        exclude = ["hospital"]


class PreviewView(APIView):
    """GET ?doctor=&patient=&rate_category=&packages=1,2 → new or follow-up,
    the charges, and what the counter needs to know before saving."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.billing.models import PatientAdvance
        from apps.finance.models import FrontDeskPolicy

        q = request.query_params
        policy = FrontDeskPolicy.for_hospital(request.user.hospital_id)
        policy_data = {
            "discount_limit_percent": policy.discount_limit_percent,
            "credit_payer_types": policy.credit_payer_types,
            "round_down_to_rupee": policy.round_down_to_rupee,
            "default_rate_category": policy.default_rate_category,
        }
        if q.get("register_only"):
            charges = [] if q.get("patient") else registration_fee_preview(request.user.hospital_id, q.get("rate_category", ""))
            return Response({
                "visit_type": None, "visit_type_label": "Registration only", "reason": "No visit: registration only",
                "last_paid_visit": None, "charges": [{"description": c.description, "amount": c.amount, "source": c.source} for c in charges],
                "total": sum((c.amount for c in charges), Decimal("0")), "warnings": [], "advance_balance": Decimal("0"), "policy": policy_data,
            })
        doctor = Doctor.objects.filter(pk=q.get("doctor"), hospital_id=request.user.hospital_id, is_active=True).first()
        if doctor is None:
            raise ValidationError({"doctor": "Choose a doctor."})
        patient = _patient(request, q["patient"]) if q.get("patient") else None
        packages = [p for p in q.get("packages", "").split(",") if p.strip().isdigit()]
        sheet = build_charges(
            hospital_id=request.user.hospital_id, patient=patient, doctor=doctor, rate_category=q.get("rate_category") or policy.default_rate_category,
            is_new_patient=patient is None, package_ids=packages, visit_type=q.get("visit_type") or None,
        )
        last = sheet.assessment.last_paid_visit
        return Response({
            "visit_type": sheet.assessment.visit_type,
            "visit_type_label": OPDVisit.VisitType(sheet.assessment.visit_type).label,
            "reason": sheet.assessment.reason,
            "last_paid_visit": timezone.localtime(last.created_at).date() if last else None,
            "charges": [{"description": c.description, "amount": c.amount, "source": c.source} for c in sheet.charges],
            "total": sheet.total,
            "warnings": sheet.warnings,
            "advance_balance": PatientAdvance.balance(patient) if patient else Decimal("0"),
            "policy": policy_data,
        })


class RegistrationView(APIView):
    """POST: register a new patient (or use `patient_id`), open the OPD visit,
    bill it and take payment, all or nothing.

    {
      "patient_id": 12 | null,
      "patient": {title, first_name, last_name, gender, date_of_birth | age_years [+ age_months], mobile, ...},
      "confirm_not_duplicate": false,
      "abha_number": "",
      "consents": [{"notice": 3, "status": "granted", "method": "written"}],
      "visit": {"doctor", "department", "visit_type", "case_type", "payer_type", "rate_category",
                "corporate_client", "tpa", "insurance_policy", "scheme", "campaign", "referred_by", "referral_note", "marketing_person"},
      "billing": {"packages": [ids], "discount_percent" | "discount_amount", "discount_reason", "approver", "approver_password",
                  "settlement": "receipt" | "credit", "payments": [{"method": "cash", "amount": 300, "reference": ""}]}
    }"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from apps.patients.registration import find_duplicates
        from apps.patients.serializers import PatientSerializer

        data = request.data
        register_only = bool(data.get("register_only"))
        check_registration_permissions(request.user, with_visit=not register_only)
        if register_only and data.get("patient_id"):
            raise ValidationError({"patient_id": "This patient is already registered. Choose a doctor to open a visit."})
        with transaction.atomic():
            if data.get("patient_id"):
                patient = _patient(request, data["patient_id"])
                is_new = False
                if data.get("patient"):
                    updates = {k: v for k, v in data["patient"].items() if k not in ("id", "uhid")}
                    serializer = PatientSerializer(patient, data=updates, partial=True)
                    serializer.is_valid(raise_exception=True)
                    patient = serializer.save()
            else:
                fields = dict(data.get("patient") or {})
                age_years, age_months = fields.pop("age_years", None), fields.pop("age_months", None)
                if not fields.get("date_of_birth") and (age_years not in (None, "") or age_months not in (None, "")):
                    fields["date_of_birth"] = estimated_dob(age_years or 0, age_months or 0).isoformat()
                    fields["dob_is_estimated"] = True
                if not data.get("confirm_not_duplicate"):
                    exact = [d for d in find_duplicates(
                        request.user.hospital_id, first_name=fields.get("first_name", ""), last_name=fields.get("last_name", ""),
                        date_of_birth=fields.get("date_of_birth"), mobile=fields.get("mobile", ""), national_id=fields.get("national_id_number", ""),
                    ) if d["match"] == "exact"]
                    if exact:
                        return Response({"detail": "This person is already registered.", "code": "duplicate_patient", "matches": exact}, status=status.HTTP_409_CONFLICT)
                serializer = PatientSerializer(data=fields)
                serializer.is_valid(raise_exception=True)
                patient = serializer.save(hospital=request.user.hospital, registration_channel="front_desk")
                is_new = True

            self._abha(request, patient, data.get("abha_number"))
            self._consents(request, patient, data.get("consents") or [])
            if register_only:
                result = register_patient_only(request=request, patient=patient, is_new_patient=is_new, data=data)
            else:
                result = register_opd_visit(request=request, patient=patient, is_new_patient=is_new, data=data)

        visit, bill = result.get("visit"), result["bill"]
        approval = result["awaiting_discount_approval"]
        return Response({
            "patient": PatientSerializer(patient).data,
            "visit": OPDVisitSerializer(visit).data if visit else None,
            "bill": {"id": bill.pk, "bill_number": bill.bill_number, "status": bill.status, "total": bill.total_amount + bill.tax_amount,
                     "discount": bill.discount_amount, "round_off": bill.round_off, "net": bill.net_amount} if bill else None,
            "assessment": result["assessment"].reason if visit else "Registered without a visit",
            "warnings": result.get("warnings", []),
            "awaiting_discount_approval": approval.pk if approval else None,
            "links": {
                "receipt": f"/api/v1/frontdesk/visits/{visit.pk}/receipt/" if visit else (f"/api/v1/billing/bills/{bill.pk}/download/" if bill else None),
                "card": f"/api/v1/frontdesk/patients/{patient.pk}/card/",
                "sticker": f"/api/v1/frontdesk/patients/{patient.pk}/sticker/",
            },
        }, status=status.HTTP_201_CREATED)

    @staticmethod
    def _abha(request, patient, abha_number):
        """Capture an ABHA the patient states; it's verified through the ABDM flow later."""
        abha_number = "".join(ch for ch in str(abha_number or "") if ch.isdigit())
        if not abha_number:
            return
        if len(abha_number) != 14:
            raise ValidationError({"abha_number": "An ABHA number has 14 digits."})
        from apps.abdm.models import AbhaLink

        formatted = f"{abha_number[:2]}-{abha_number[2:6]}-{abha_number[6:10]}-{abha_number[10:]}"
        AbhaLink.objects.update_or_create(
            patient=patient, defaults={"hospital": patient.hospital, "abha_number": formatted, "status": AbhaLink.Status.PENDING,
                                       "verification_method": AbhaLink.VerificationMethod.EXISTING_ABHA, "initiated_by": request.user},
        )

    @staticmethod
    def _consents(request, patient, consents):
        from apps.privacy.serializers import PatientConsentSerializer
        from apps.privacy.services import sync_messaging_opt_out

        for answer in consents:
            serializer = PatientConsentSerializer(data={**answer, "patient": patient.pk}, context={"request": request})
            serializer.is_valid(raise_exception=True)
            consent = serializer.save(hospital=patient.hospital, captured_by=request.user)
            sync_messaging_opt_out(consent, request.user)


class OPDVisitViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Today's OPD register (or ?date=YYYY-MM-DD), newest first."""

    serializer_class = OPDVisitSerializer
    queryset = OPDVisit.objects.select_related("patient", "doctor", "department", "appointment", "bill")
    filterset_fields = ["doctor", "payer_type", "visit_type", "patient"]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == "list" and not self.request.query_params.get("patient"):
            day = self.request.query_params.get("date") or timezone.localdate().isoformat()
            qs = qs.filter(created_at__date=day)
        return qs

    @action(detail=True, methods=["get"])
    def receipt(self, request, pk=None):
        visit = self.get_object()
        return _pdf(frontdesk_print.opd_receipt(visit), f"{visit.visit_number}.pdf")


class PatientCardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk, kind):
        if not request.user.has_perm("patients.view_patient"):
            raise PermissionDenied()
        patient = _patient(request, pk)
        if kind == "card":
            return _pdf(frontdesk_print.registration_card(patient), f"{patient.uhid}-card.pdf")
        return _pdf(frontdesk_print.uhid_sticker(patient, request.query_params.get("copies", 1)), f"{patient.uhid}-stickers.pdf")


class AdvanceView(APIView):
    """GET ?patient= → balance and ledger. POST a deposit or refund."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.billing.models import PatientAdvance

        patient = _patient(request, request.query_params.get("patient"))
        rows = PatientAdvance.objects.filter(patient=patient).values("id", "kind", "amount", "payment_method", "reference", "receipt_number", "bill__bill_number", "created_at")
        return Response({"balance": PatientAdvance.balance(patient), "entries": list(rows)})

    def post(self, request):
        from apps.billing.models import Payment, PatientAdvance

        if not request.user.has_perm("billing.add_bill"):
            raise PermissionDenied()
        patient = _patient(request, request.data.get("patient"))
        kind = request.data.get("kind", PatientAdvance.Kind.DEPOSIT)
        amount = money(request.data.get("amount"))
        method = request.data.get("payment_method", Payment.PaymentMethod.CASH)
        if kind not in (PatientAdvance.Kind.DEPOSIT, PatientAdvance.Kind.REFUND):
            raise ValidationError({"kind": "Record a deposit or a refund; adjustments happen when a bill is paid."})
        if amount <= 0:
            raise ValidationError({"amount": "Enter an amount."})
        if method not in Payment.PaymentMethod.values or method == Payment.PaymentMethod.ADVANCE:
            raise ValidationError({"payment_method": "Choose how the money was received or paid back."})
        if kind == PatientAdvance.Kind.REFUND and amount > PatientAdvance.balance(patient):
            raise ValidationError({"amount": "That's more than the advance balance."})
        entry = PatientAdvance.objects.create(hospital=request.user.hospital, patient=patient, kind=kind, amount=amount,
                                              payment_method=method, reference=str(request.data.get("reference", ""))[:100], recorded_by=request.user)
        return Response({"id": entry.pk, "receipt_number": entry.receipt_number, "balance": PatientAdvance.balance(patient)}, status=status.HTTP_201_CREATED)


class DiscountApprovalViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Discounts above the limit. Approvers see and decide them; the counter
    sees its own requests."""

    from apps.billing.models import DiscountApproval as _Model

    queryset = _Model.objects.select_related("bill", "bill__patient", "requested_by", "approver")
    filterset_fields = ["status"]

    def get_serializer_class(self):
        from apps.billing.models import DiscountApproval

        class Serializer(serializers.ModelSerializer):
            bill_number = serializers.CharField(source="bill.bill_number", read_only=True)
            patient_name = serializers.CharField(source="bill.patient.full_name", read_only=True)
            gross = serializers.SerializerMethodField()
            requested_by_name = serializers.CharField(source="requested_by.get_full_name", read_only=True, default=None)

            class Meta:
                model = DiscountApproval
                exclude = ["hospital"]

            def get_gross(self, obj):
                return obj.bill.total_amount + obj.bill.tax_amount

        return Serializer

    def get_queryset(self):
        qs = super().get_queryset()
        if not self.request.user.has_perm("billing.authorize_discount"):
            qs = qs.filter(requested_by=self.request.user)
        return qs

    def _decide(self, request, approve):
        from apps.billing.models import Bill, DiscountApproval
        from apps.billing.services import recalculate_bill

        if not request.user.has_perm("billing.authorize_discount"):
            raise PermissionDenied("You can't authorise discounts.")
        approval = self.get_object()
        if approval.status != DiscountApproval.Status.PENDING:
            raise ValidationError({"detail": "Already decided."})
        approval.status = DiscountApproval.Status.APPROVED if approve else DiscountApproval.Status.REJECTED
        approval.approver, approval.decided_at = request.user, timezone.now()
        approval.save(update_fields=["status", "approver", "decided_at"])
        bill = approval.bill
        if not approve:
            bill.discount_amount, bill.discount_reason = Decimal("0"), ""
            bill.save(update_fields=["discount_amount", "discount_reason"])
        bill.status = Bill.Status.UNPAID
        bill.save(update_fields=["status"])
        recalculate_bill(bill)
        return Response(self.get_serializer(approval).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._decide(request, True)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._decide(request, False)


class ApproverListView(APIView):
    """Who can authorise a discount at this counter."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.accounts.models import User

        people = [u for u in User.objects.filter(hospital_id=request.user.hospital_id, is_active=True, is_blocked=False) if u.has_perm("billing.authorize_discount")]
        return Response([{"id": u.pk, "name": u.get_full_name() or u.email} for u in people])


def _fee_rule_serializer():
    from apps.finance.models import ConsultationFeeRule

    return model_serializer(ConsultationFeeRule)


class ConsultationFeeRuleViewSet(TenantCRUDViewSet):
    """Finance → Consultation fees (finance permissions)."""

    from apps.finance.models import ConsultationFeeRule as _Model

    queryset = _Model.objects.select_related("doctor", "department", "new_visit_tariff", "follow_up_tariff")
    filterset_fields = ["doctor", "department", "is_active"]

    def get_serializer_class(self):
        return _fee_rule_serializer()


class FrontDeskPolicyView(APIView):
    """GET for everyone at the desk; PATCH for finance administrators."""

    permission_classes = [IsAuthenticated]
    FIELDS = ("registration_fee_tariff", "discount_limit_percent", "credit_payer_types", "round_down_to_rupee", "default_rate_category",
              "portal_payment", "portal_payment_hold_minutes")

    def _data(self, policy):
        return {f: getattr(policy, f + "_id" if f == "registration_fee_tariff" else f) for f in self.FIELDS}

    def get(self, request):
        from apps.finance.models import FrontDeskPolicy

        return Response(self._data(FrontDeskPolicy.for_hospital(request.user.hospital_id)))

    def patch(self, request):
        from apps.finance.models import FrontDeskPolicy, ServiceTariff

        if not request.user.has_perm("finance.change_frontdeskpolicy"):
            raise PermissionDenied("Only finance administrators change front-desk rules.")
        policy = FrontDeskPolicy.for_hospital(request.user.hospital_id)
        for f in self.FIELDS:
            if f not in request.data:
                continue
            value = request.data[f]
            if f == "registration_fee_tariff":
                if value and not ServiceTariff.objects.filter(pk=value, hospital_id=request.user.hospital_id).exists():
                    raise ValidationError({f: "Tariff not found."})
                policy.registration_fee_tariff_id = value or None
            else:
                setattr(policy, f, value)
        policy.full_clean(exclude=["hospital"])
        policy.save()
        return Response(self._data(policy))
