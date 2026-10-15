from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import ActionPermissionRequired
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin
from .models import Bill, BillItem, InsuranceClaim, Payment
from .serializers import BillItemSerializer, BillSerializer, CreditNoteSerializer, InsuranceClaimSerializer, PaymentSerializer


class BillViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, ActionPermissionRequired]
    action_permissions = {
        "list": "billing.view_bill",
        "retrieve": "billing.view_bill",
        "create": "billing.add_bill",
        "update": "billing.change_bill",
        "partial_update": "billing.change_bill",
        "add_item": "billing.change_bill",
        "download": "billing.view_bill",
        "interim": "billing.add_bill",
        "bed_charges": "billing.view_bill",
        "post_bed_charges": "billing.change_bill",
        "hold": "billing.hold_bill",
        "release": "billing.hold_bill",
        "lock": "billing.lock_bill",
        "unlock": "billing.unlock_bill",
        "hold_history": "billing.view_bill",
        "change_category": "billing.change_bill",
        "credit_note": "billing.change_bill",
        "credit_notes": "billing.view_bill",
        "payer_split": "billing.view_bill",
    }
    serializer_class = BillSerializer
    queryset = Bill.objects.all()
    filterset_fields = ["patient", "admission", "status"]
    audited_fields = ("total_amount", "net_amount", "status")

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)
        self._log("create", serializer.instance)

    def perform_update(self, serializer):
        from .adjustments import ensure_open

        ensure_open(serializer.instance)
        super().perform_update(serializer)

    @action(detail=True, methods=["post"])
    def hold(self, request, pk=None):
        """POST {reason} — stop charges, payments, discounts and discharge until released."""
        from .adjustments import hold

        bill = hold(self.get_object(), request.user, request.data.get("reason"))
        self._log("update", bill)
        return Response(BillSerializer(bill).data)

    @action(detail=True, methods=["post"])
    def release(self, request, pk=None):
        """POST {note}"""
        from .adjustments import release

        bill = release(self.get_object(), request.user, request.data.get("note"))
        self._log("update", bill)
        return Response(BillSerializer(bill).data)

    @action(detail=True, methods=["post"])
    def lock(self, request, pk=None):
        """POST {note?} — finalise the bill (releases a hold)."""
        from .adjustments import lock

        bill = lock(self.get_object(), request.user, request.data.get("note", ""))
        self._log("update", bill)
        return Response(BillSerializer(bill).data)

    @action(detail=True, methods=["post"])
    def unlock(self, request, pk=None):
        """POST {reason} — reopen a locked bill (billing.unlock_bill)."""
        from .adjustments import unlock

        bill = unlock(self.get_object(), request.user, request.data.get("reason"))
        self._log("update", bill)
        return Response(BillSerializer(bill).data)

    @action(detail=True, methods=["get"], url_path="hold-history")
    def hold_history(self, request, pk=None):
        bill = self.get_object()
        return Response([{"action": e.action, "reason": e.reason, "by": e.by.get_full_name() if e.by_id else "", "at": e.created_at}
                         for e in bill.hold_events.select_related("by")])

    @action(detail=True, methods=["post"], url_path="change-category")
    def change_category(self, request, pk=None):
        """POST {category, preview?: true} — re-price the tariff lines for the new patient category."""
        from .adjustments import change_category

        bill = self.get_object()
        result = change_category(bill, str(request.data.get("category", "")), request.user, apply=not request.data.get("preview"))
        if result["applied"]:
            self._log("update", bill)
        return Response(result)

    @action(detail=True, methods=["post"], url_path="credit-note")
    def credit_note(self, request, pk=None):
        """POST {reason, amount? | items?: [{item, amount}], settlement: reduce_due|advance|refund, refund_method?, refund_reference?}
        — amounts are before GST; with only `amount` it's spread across the bill's services."""
        from .adjustments import credit_note

        bill = self.get_object()
        d = request.data
        note = credit_note(bill, request.user, reason=d.get("reason", ""), amount=d.get("amount"), items=d.get("items") or None,
                           settlement=d.get("settlement") or "reduce_due", refund_method=str(d.get("refund_method", "")),
                           refund_reference=str(d.get("refund_reference", "")))
        self._log("update", bill)
        bill.refresh_from_db()
        return Response({"credit_note": CreditNoteSerializer(note).data, "bill": BillSerializer(bill).data}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="payer-split")
    def payer_split(self, request, pk=None):
        """Insurer-payable vs patient-payable: lines on the insurer's non-payable
        lists (apps.tpa.non_payable) are re-checked against the current lists."""
        from apps.tpa import non_payable

        bill = self.get_object()
        amount = non_payable.flag_bill(bill)
        if amount != bill.non_payable_amount:
            bill.non_payable_amount = amount
            bill.save(update_fields=["non_payable_amount"])
        return Response(non_payable.split(bill))

    @action(detail=True, methods=["get"], url_path="credit-notes")
    def credit_notes(self, request, pk=None):
        return Response(CreditNoteSerializer(self.get_object().credit_notes.all(), many=True).data)

    @action(detail=True, methods=["post"], url_path="add-item")
    def add_item(self, request, pk=None):
        bill = self.get_object()
        from .adjustments import ensure_open

        ensure_open(bill, "add charges")
        desc = request.data.get("description")
        qty = request.data.get("quantity", 1)
        price = request.data.get("unit_price")
        extra = {}
        if request.data.get("tariff"):
            # NABH FPM.3.a — rate, SAC/HSN and GST come from the tariff master,
            # priced for the bill's patient category.
            from apps.finance.models import ServiceTariff

            tariff = ServiceTariff.objects.filter(pk=request.data["tariff"], hospital_id=bill.hospital_id, is_active=True).first()
            if tariff is None:
                return Response({"tariff": "Not found."}, status=status.HTTP_400_BAD_REQUEST)
            desc = desc or tariff.name
            price = price or tariff.rate_for(bill.patient_category)
            extra = {"tariff": tariff, "hsn_sac": tariff.hsn_sac, "gst_rate": tariff.gst_rate}

        if not desc or not price:
            return Response({"error": "description and unit_price are required"}, status=status.HTTP_400_BAD_REQUEST)
        if request.data.get("doctor"):
            # The rendering doctor — what doctor payouts are computed from.
            from apps.appointments.models import Doctor

            doctor = Doctor.objects.filter(pk=request.data["doctor"], hospital_id=bill.hospital_id).first()
            if doctor is None:
                return Response({"doctor": "Not found."}, status=status.HTTP_400_BAD_REQUEST)
            extra["doctor"] = doctor
        if request.data.get("service_date"):
            extra["service_date"] = request.data["service_date"]

        item = BillItem.objects.create(
            bill=bill,
            description=desc,
            quantity=int(qty),
            unit_price=price,
            total_price=0,
            **extra,
        )
        from .services import recalculate_bill

        recalculate_bill(bill)

        return Response(BillItemSerializer(item).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, pk=None):
        bill = self.get_object()
        from django.http import HttpResponse
        from .bill_pdf import render_bill_pdf

        pdf_bytes = render_bill_pdf(bill)
        filename = f"Bill_{bill.id}_{getattr(bill.patient, 'uhid', 'patient')}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response



class PaymentViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, ActionPermissionRequired]
    action_permissions = {
        "list": "billing.view_payment",
        "retrieve": "billing.view_payment",
        "create": "billing.add_payment",
    }
    serializer_class = PaymentSerializer
    queryset = Payment.objects.all()
    filterset_fields = ["bill", "payment_method"]
    audited_fields = ("amount", "payment_method", "transaction_id")

    def perform_create(self, serializer):
        from .adjustments import ensure_open

        ensure_open(serializer.validated_data["bill"], "take a payment", payment=True)
        hospital = getattr(self.request.user, "hospital", None)
        payment = serializer.save(hospital=hospital)
        self._log("create", payment)

        from .online_payments import refresh_bill_status

        refresh_bill_status(payment.bill)


class InsuranceClaimViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, ActionPermissionRequired]
    action_permissions = {
        "list": "billing.view_insuranceclaim",
        "retrieve": "billing.view_insuranceclaim",
        "create": "billing.add_insuranceclaim",
        "update": "billing.change_insuranceclaim",
        "partial_update": "billing.change_insuranceclaim",
    }
    serializer_class = InsuranceClaimSerializer
    queryset = InsuranceClaim.objects.all()
    filterset_fields = ["bill", "status", "insurance_company"]
    audited_fields = ("claimed_amount", "approved_amount", "status")

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)
        self._log("create", serializer.instance)



def interim(self, request):
    """NABH AAC.6.e — interim bill for an admission on request: a snapshot
    of every charge raised so far and payments received, without closing
    the running bill."""
    from decimal import Decimal

    from apps.ipd.models import Admission

    adm = Admission.objects.filter(pk=request.data.get("admission"), hospital_id=request.user.hospital_id).first()
    if adm is None:
        return Response({"admission": "Not found."}, status=status.HTTP_400_BAD_REQUEST)
    from .bed_charges import policy_for, post_bed_charges

    if policy_for(adm.hospital_id).auto_post:
        post_bed_charges(adm)  # the snapshot includes bed-days up to now
    running = Bill.objects.filter(admission=adm, is_interim=False).exclude(status="cancelled")
    interim = Bill.objects.create(hospital_id=adm.hospital_id, patient=adm.patient, admission=adm, is_interim=True, status="draft")
    for item in BillItem.objects.filter(bill__in=running):
        # Interim lines are a snapshot for the patient, never payout-eligible
        # (the running bill's own line is what gets paid out).
        BillItem.objects.create(bill=interim, description=item.description, quantity=item.quantity, unit_price=item.unit_price, total_price=0,
                                hsn_sac=item.hsn_sac, gst_rate=item.gst_rate, tariff=item.tariff,
                                service_date=item.service_date, source="interim")
    items = list(interim.items.all())
    interim.total_amount = sum((i.total_price for i in items), Decimal("0"))
    interim.tax_amount = sum((i.tax_amount for i in items), Decimal("0"))
    interim.net_amount = interim.total_amount + interim.tax_amount
    interim.save()
    paid = sum((p.amount for p in Payment.objects.filter(bill__in=running)), Decimal("0"))
    data = BillSerializer(interim).data
    data["payments_received"] = float(paid)
    data["balance_due"] = float(interim.net_amount - paid)
    return Response(data, status=status.HTTP_201_CREATED)


BillViewSet.interim = action(detail=False, methods=["post"])(interim)


def _admission_for(request, admission_id):
    from apps.ipd.models import Admission

    return Admission.objects.filter(pk=admission_id, hospital_id=request.user.hospital_id).select_related("bed__room__ward").first()


def _bed_charge_payload(result, bill=None):
    p = result["policy"]
    return {
        "bill": bill.pk if bill else None,
        "bill_number": bill.bill_number if bill else None,
        "policy": {"cycle": p.cycle, "grace_hours": p.grace_hours, "checkout_hour": p.checkout_hour, "transfer_day_rule": p.transfer_day_rule},
        "bed_days": len(result["days"]),
        "days": result["days"],
        "lines": [{"description": ln["description"], "tariff": ln["tariff"].pk, "days": ln["days"], "unit_price": float(ln["unit_price"]),
                   "amount": float(ln["unit_price"] * ln["days"])} for ln in result["lines"]],
        "total": float(sum(ln["unit_price"] * ln["days"] for ln in result["lines"])),
        "unpriced_beds": result["unpriced_beds"],
    }


def bed_charges(self, request, admission_id=None):
    """GET preview of an admission's bed / room-rent charges (nothing is saved)."""
    from .bed_charges import compute_bed_charges

    adm = _admission_for(request, admission_id)
    if adm is None:
        return Response({"admission": "Not found."}, status=status.HTTP_404_NOT_FOUND)
    bill = Bill.objects.filter(admission=adm, is_interim=False).exclude(status=Bill.Status.CANCELLED).order_by("created_at").first()
    return Response(_bed_charge_payload(compute_bed_charges(adm, category=bill.patient_category if bill else "general"), bill))


def post_bed_charges(self, request, admission_id=None):
    """POST (re-)posts bed charges to the admission's running bill — idempotent."""
    from .bed_charges import post_bed_charges as post

    adm = _admission_for(request, admission_id)
    if adm is None:
        return Response({"admission": "Not found."}, status=status.HTTP_404_NOT_FOUND)
    bill, result = post(adm)
    return Response(_bed_charge_payload(result, bill))


BillViewSet.bed_charges = action(detail=False, methods=["get"], url_path=r"bed-charges/(?P<admission_id>\d+)")(bed_charges)
BillViewSet.post_bed_charges = action(detail=False, methods=["post"], url_path=r"bed-charges/(?P<admission_id>\d+)/post")(post_bed_charges)


from apps.core.crud import TenantCRUDViewSet, model_serializer  # noqa: E402

from .models import BedBillingPolicy, BedChargeRule  # noqa: E402


class BedChargeRuleViewSet(TenantCRUDViewSet):
    """Which tariff(s) each bed-day attracts — by ward, bed type or catch-all."""

    serializer_class = model_serializer(BedChargeRule, extra={
        "tariff_name": serializers.CharField(source="tariff.name", read_only=True),
        "ward_name": serializers.CharField(source="ward.name", read_only=True, default=""),
    })
    queryset = BedChargeRule.objects.select_related("tariff", "ward")
    filterset_fields = ["ward", "bed_type", "is_active"]
    audited_fields = ("tariff", "ward", "bed_type", "is_active")


class BedBillingPolicyView(APIView):
    """GET / PUT the hospital's bed-day counting policy (defaults until saved)."""

    permission_classes = [permissions.IsAuthenticated]
    fields = ("cycle", "grace_hours", "checkout_hour", "transfer_day_rule", "auto_post")

    def get(self, request):
        from .bed_charges import policy_for

        p = policy_for(request.user.hospital_id)
        return Response({f: getattr(p, f) for f in self.fields})

    def put(self, request):
        if not request.user.has_perm("billing.change_bill"):
            return Response({"detail": "You do not have permission to change the bed billing policy."}, status=status.HTTP_403_FORBIDDEN)
        policy, _ = BedBillingPolicy.objects.get_or_create(hospital_id=request.user.hospital_id)
        errors = {}
        for f in self.fields:
            if f not in request.data:
                continue
            value = request.data[f]
            if f == "cycle" and value not in BedBillingPolicy.Cycle.values:
                errors[f] = f"One of {BedBillingPolicy.Cycle.values}"
            elif f == "transfer_day_rule" and value not in BedBillingPolicy.TransferRule.values:
                errors[f] = f"One of {BedBillingPolicy.TransferRule.values}"
            elif f in ("grace_hours", "checkout_hour") and not (str(value).isdigit() and int(value) <= 23):
                errors[f] = "A whole number of hours, 0–23."
            elif f == "auto_post":
                policy.auto_post = value in (True, "true", "True", 1, "1")
            else:
                setattr(policy, f, int(value) if f in ("grace_hours", "checkout_hour") else value)
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)
        policy.save()
        return self.get(request)


class BillHoldStatusView(APIView):
    """GET /billing/hold-status/?patient=<id> — whether the patient has a bill
    on hold, for the "Bill on hold" badge in reception, EMR, pharmacy, IPD and
    insurance screens. Open to any signed-in staff of the hospital: it shows
    only the bill number and the reason, never amounts."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from apps.patients.models import Patient

        from .adjustments import held_bills

        patient = Patient.objects.filter(pk=request.query_params.get("patient") or 0, hospital_id=request.user.hospital_id).first()
        if patient is None:
            return Response({"on_hold": False, "bills": []})
        bills = [{"id": b.pk, "bill_number": b.bill_number, "reason": b.hold_reason, "held_at": b.held_at} for b in held_bills(patient)[:5]]
        return Response({"on_hold": bool(bills), "bills": bills})
