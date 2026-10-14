from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.crud import ADMIN_PERMISSION_CLASSES, TenantCRUDViewSet, model_serializer
from apps.core.permissions import RequiresViewPermission
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin

from . import payout_statements, payouts
from .models_payout import DoctorPayout, DoctorPayoutDeduction, DoctorPayoutLine, DoctorPayoutRule, PayoutDeductionHead, PayoutStatementDelivery


class DoctorPayoutRuleViewSet(TenantCRUDViewSet):
    """Revenue-share / professional-fee rules. Most specific match wins."""

    permission_classes = TenantCRUDViewSet.permission_classes + [RequiresViewPermission]
    serializer_class = model_serializer(DoctorPayoutRule, extra={
        "doctor_name": serializers.CharField(source="doctor.name", read_only=True, default=""),
        "tariff_name": serializers.CharField(source="tariff.name", read_only=True, default=""),
    })
    queryset = DoctorPayoutRule.objects.select_related("doctor", "tariff")
    filterset_fields = ["doctor", "tariff", "is_active", "earned_on"]
    # Editing a rule only affects statements generated afterwards — live
    # statements keep the amounts and rule label they were created with.
    audited_fields = ("doctor", "tariff", "service_department", "basis", "value", "earned_on", "deduct_medicine_cost", "is_active")


class PayoutDeductionHeadSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayoutDeductionHead
        exclude = ["hospital"]

    def validate_ledger_code(self, code):
        from .accounting import ensure_accounts

        if code not in ensure_accounts(self.context["request"].user.hospital_id):
            raise serializers.ValidationError("No ledger account with this code. Add it under Accounts > Ledgers first.")
        return code

    def validate_default_amount(self, value):
        if value < 0:
            raise serializers.ValidationError("Can't be negative.")
        return value


class PayoutDeductionHeadViewSet(TenantCRUDViewSet):
    """Deduction heads for consultant payouts (advance recovery, room rent share...)."""

    permission_classes = TenantCRUDViewSet.permission_classes + [RequiresViewPermission]
    serializer_class = PayoutDeductionHeadSerializer
    queryset = PayoutDeductionHead.objects.all()
    filterset_fields = ["is_active", "auto_apply"]
    audited_fields = ("name", "ledger_code", "default_amount", "auto_apply", "is_active")


LineSerializer = model_serializer(DoctorPayoutLine)


class DeductionSerializer(serializers.ModelSerializer):
    head_name = serializers.CharField(source="head.name", read_only=True)

    class Meta:
        model = DoctorPayoutDeduction
        fields = ["id", "head", "head_name", "amount", "remarks", "created_at"]


class DeliverySerializer(serializers.ModelSerializer):
    sent_by_name = serializers.CharField(source="sent_by.get_full_name", read_only=True, default="")

    class Meta:
        model = PayoutStatementDelivery
        fields = ["id", "channel", "recipient", "sent", "error", "sent_by_name", "created_at"]


class DoctorPayoutSerializer(serializers.ModelSerializer):
    doctor_name = serializers.CharField(source="doctor.name", read_only=True)
    line_count = serializers.IntegerField(source="lines.count", read_only=True)
    deductions = DeductionSerializer(many=True, read_only=True)
    last_sent_at = serializers.SerializerMethodField()

    def get_last_sent_at(self, obj):
        last = obj.deliveries.filter(sent=True).first()
        return last.created_at if last else None

    class Meta:
        model = DoctorPayout
        fields = "__all__"
        read_only_fields = [f.name for f in DoctorPayout._meta.fields if f.name not in ("notes", "tds_percent")]


def _parse_date(value, name):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise serializers.ValidationError({name: "YYYY-MM-DD"})


class DoctorPayoutViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                          mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """Payout statements. Created by `generate`, never by hand (so there is
    no create or delete route); editable only for notes / TDS % while draft."""

    permission_classes = ADMIN_PERMISSION_CLASSES + [RequiresViewPermission]
    serializer_class = DoctorPayoutSerializer
    queryset = DoctorPayout.objects.select_related("doctor")
    filterset_fields = ["doctor", "status", "period_start", "period_end"]
    audited_fields = ("status", "gross_payout", "net_payable", "tds_percent", "payment_reference")
    action_permissions = {
        "generate": "finance.add_doctorpayout",
        "preview": "finance.view_doctorpayout",
        "approve": "finance.change_doctorpayout",
        "mark_paid": "finance.change_doctorpayout",
        "cancel": "finance.change_doctorpayout",
        "lines": "finance.view_doctorpayout",
        "statement": "finance.view_doctorpayout",
        "deliveries": "finance.view_doctorpayout",
        "tds_report": "finance.view_doctorpayout",
        "add_deduction": "finance.change_doctorpayout",
        "remove_deduction": "finance.change_doctorpayout",
        "send": "finance.change_doctorpayout",
        "send_statements": "finance.change_doctorpayout",
    }

    def perform_update(self, serializer):
        if serializer.instance.status != DoctorPayout.Status.DRAFT:
            raise serializers.ValidationError({"status": "Only a draft statement can be edited."})
        payout = serializer.save()
        payouts.refresh_totals(payout)

    def _period(self, request):
        start = _parse_date(request.data.get("period_start") or request.query_params.get("period_start"), "period_start")
        end = _parse_date(request.data.get("period_end") or request.query_params.get("period_end"), "period_end")
        if start > end:
            raise serializers.ValidationError({"period_end": "Must be on or after period_start."})
        return start, end

    def _doctor(self, request):
        from apps.appointments.models import Doctor

        doctor_id = request.data.get("doctor") or request.query_params.get("doctor")
        if not doctor_id:
            return None
        doctor = Doctor.objects.filter(pk=doctor_id, hospital_id=request.user.hospital_id).first()
        if doctor is None:
            raise serializers.ValidationError({"doctor": "Not found."})
        return doctor

    @action(detail=False, methods=["get"])
    def preview(self, request):
        """GET ?period_start=&period_end=[&doctor=] — what a run would pay, per doctor. Nothing is saved."""
        start, end = self._period(request)
        rows = payouts.eligible_lines(request.user.hospital_id, start, end, self._doctor(request))
        totals = {}
        for item, _rule, _earned, base, amount, _med in rows:
            t = totals.setdefault(item.doctor_id, {"doctor": item.doctor_id, "doctor_name": item.doctor.name, "lines": 0, "base_amount": Decimal("0"), "gross_payout": Decimal("0")})
            t["lines"] += 1
            t["base_amount"] += base
            t["gross_payout"] += amount
        return Response({"period_start": start, "period_end": end, "doctors": sorted(totals.values(), key=lambda t: t["doctor_name"])})

    @action(detail=False, methods=["post"])
    def generate(self, request):
        """POST {period_start, period_end, doctor?, tds_percent?} — draft statements for the period."""
        start, end = self._period(request)
        try:
            tds = Decimal(str(request.data.get("tds_percent", "10")))
        except InvalidOperation:
            raise serializers.ValidationError({"tds_percent": "A number."})
        if not Decimal("0") <= tds <= Decimal("30"):
            raise serializers.ValidationError({"tds_percent": "0–30."})
        statements = payouts.generate_statements(request.user.hospital_id, start, end, doctor=self._doctor(request), tds_percent=tds)
        for s in statements:
            self._log("create", s)
        return Response(DoctorPayoutSerializer(statements, many=True).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def lines(self, request, pk=None):
        """The statement's lines; ?output=xlsx for a spreadsheet to send the doctor."""
        payout = self.get_object()
        rows = list(payout.lines.all())
        if request.query_params.get("output") != "xlsx":
            return Response(LineSerializer(rows, many=True).data)
        from apps.core.xlsx import build_xlsx

        sheet = [["Date", "Bill", "Patient", "Service", "Qty", "Net amount", "Rule", "Payout"]]
        sheet += [[str(r.service_date), r.bill_number, r.patient_name, r.description, r.quantity, float(r.base_amount), r.rule_label, float(r.payout_amount)] for r in rows]
        sheet += [[], ["", "", "", "", "", "Gross payout", "", float(payout.gross_payout)],
                  ["", "", "", "", "", f"TDS {payout.tds_percent:g}%", "", -float(payout.tds_amount)],
                  ["", "", "", "", "", "Net payable", "", float(payout.net_payable)]]
        resp = HttpResponse(build_xlsx(sheet, "Payout statement"), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="payout-{payout.doctor.name}-{payout.period_start}-{payout.period_end}.xlsx"'
        return resp

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        payout = self.get_object()
        if payout.status != DoctorPayout.Status.DRAFT:
            return Response({"status": "Only a draft can be approved."}, status=status.HTTP_400_BAD_REQUEST)
        payout.status, payout.approved_by, payout.approved_at = DoctorPayout.Status.APPROVED, request.user, timezone.now()
        payout.save(update_fields=["status", "approved_by", "approved_at"])
        self._log("update", payout)
        return Response(DoctorPayoutSerializer(payout).data)

    @action(detail=True, methods=["post"], url_path="mark-paid")
    @transaction.atomic
    def mark_paid(self, request, pk=None):
        """POST {payment_mode, payment_reference, paid_on?} — also posts the accounting voucher."""
        payout = self.get_object()
        if payout.status != DoctorPayout.Status.APPROVED:
            return Response({"status": "Approve the statement before paying it."}, status=status.HTTP_400_BAD_REQUEST)
        mode = request.data.get("payment_mode", "neft")
        if mode not in ("neft", "upi", "cheque", "cash"):
            return Response({"payment_mode": "neft | upi | cheque | cash"}, status=status.HTTP_400_BAD_REQUEST)
        if mode != "cash" and not request.data.get("payment_reference"):
            return Response({"payment_reference": "UTR / cheque number is required."}, status=status.HTTP_400_BAD_REQUEST)
        payout.paid_on = _parse_date(request.data["paid_on"], "paid_on") if request.data.get("paid_on") else timezone.localdate()
        payout.payment_mode, payout.payment_reference = mode, request.data.get("payment_reference", "")
        payout.status = DoctorPayout.Status.PAID
        payout.save(update_fields=["paid_on", "payment_mode", "payment_reference", "status"])
        payouts.post_payout_journal(payout)
        self._log("update", payout)
        return Response(DoctorPayoutSerializer(payout).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Draft or approved only; its services become payable on the next run."""
        payout = self.get_object()
        if payout.status not in (DoctorPayout.Status.DRAFT, DoctorPayout.Status.APPROVED):
            return Response({"status": "A paid statement cannot be cancelled."}, status=status.HTTP_400_BAD_REQUEST)
        payout.lines.all().delete()
        payout.status = DoctorPayout.Status.CANCELLED
        payout.save(update_fields=["status"])
        payouts.refresh_totals(payout)
        self._log("update", payout)
        return Response(DoctorPayoutSerializer(payout).data)

    # --- deduction heads on a statement ---------------------------------

    def _draft(self):
        payout = self.get_object()
        if payout.status != DoctorPayout.Status.DRAFT:
            raise serializers.ValidationError({"status": "Deductions can only be changed on a draft statement."})
        return payout

    @action(detail=True, methods=["post"], url_path="add-deduction")
    @transaction.atomic
    def add_deduction(self, request, pk=None):
        """POST {head, amount?, remarks?} — amount defaults to the head's default."""
        payout = self._draft()
        head = PayoutDeductionHead.objects.filter(pk=request.data.get("head"), hospital_id=request.user.hospital_id, is_active=True).first()
        if head is None:
            raise serializers.ValidationError({"head": "Pick an active deduction head."})
        try:
            amount = Decimal(str(request.data.get("amount") or head.default_amount))
        except InvalidOperation:
            raise serializers.ValidationError({"amount": "A number."})
        if amount <= 0:
            raise serializers.ValidationError({"amount": "Must be more than 0."})
        if amount > payout.net_payable:
            raise serializers.ValidationError({"amount": f"More than the net payable (₹{payout.net_payable})."})
        DoctorPayoutDeduction.objects.create(payout=payout, head=head, amount=amount, remarks=str(request.data.get("remarks", ""))[:255], added_by=request.user)
        payouts.refresh_totals(payout)
        self._log("update", payout)
        return Response(DoctorPayoutSerializer(payout).data)

    @action(detail=True, methods=["post"], url_path="remove-deduction")
    @transaction.atomic
    def remove_deduction(self, request, pk=None):
        """POST {deduction: id} — or {head: id} to remove the latest one of that head."""
        payout = self._draft()
        if request.data.get("deduction"):
            target = payout.deductions.filter(pk=request.data["deduction"]).first()
        else:
            target = payout.deductions.filter(head_id=request.data.get("head")).order_by("-id").first()
        deleted = target.delete()[0] if target else 0
        if not deleted:
            raise serializers.ValidationError({"deduction": "Not on this statement."})
        payouts.refresh_totals(payout)
        self._log("update", payout)
        return Response(DoctorPayoutSerializer(payout).data)

    # --- statement PDF and sending ------------------------------------------

    @action(detail=True, methods=["get"])
    def statement(self, request, pk=None):
        payout = self.get_object()
        resp = HttpResponse(payout_statements.statement_pdf(payout), content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="{payout_statements.statement_filename(payout)}"'
        return resp

    def _channels(self, request):
        channels = request.data.get("channels") or ["email"]
        if isinstance(channels, str):
            channels = [c.strip() for c in channels.split(",") if c.strip()]
        if not channels or set(channels) - {"email", "whatsapp"}:
            raise serializers.ValidationError({"channels": "email and/or whatsapp."})
        return channels

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        """POST {channels: ["email", "whatsapp"]} — send this statement to the consultant."""
        payout = self.get_object()
        if payout.status not in payout_statements.SENDABLE:
            return Response({"status": "Approve the statement before sending it."}, status=status.HTTP_400_BAD_REQUEST)
        rows = payout_statements.send_statement(payout, self._channels(request), request.user)
        return Response(DeliverySerializer(rows, many=True).data)

    @action(detail=False, methods=["post"], url_path="send-statements")
    def send_statements(self, request):
        """POST {channels, ids?: [...]} or {channels, period_start, period_end, doctor?}:
        send every approved/paid statement chosen. Up to 10 are sent at once;
        more are queued and the results land in each statement's deliveries."""
        channels = self._channels(request)
        qs = self.get_queryset().filter(status__in=payout_statements.SENDABLE)
        if request.data.get("ids"):
            qs = qs.filter(pk__in=request.data["ids"])
        else:
            start, end = self._period(request)
            qs = qs.filter(period_start__gte=start, period_end__lte=end)
            doctor = self._doctor(request)
            if doctor:
                qs = qs.filter(doctor=doctor)
        ids = list(qs.values_list("pk", flat=True))
        if not ids:
            return Response({"detail": "No approved or paid statements match."}, status=status.HTTP_400_BAD_REQUEST)
        if len(ids) <= payout_statements.INLINE_SEND_LIMIT:
            return Response({"statements": len(ids), **payout_statements.send_many(ids, channels, request.user.pk)})
        from .tasks import send_payout_statements

        send_payout_statements.delay(ids, channels, request.user.pk)
        return Response({"statements": len(ids), "queued": True}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["get"])
    def deliveries(self, request, pk=None):
        return Response(DeliverySerializer(self.get_object().deliveries.select_related("sent_by"), many=True).data)

    # --- TDS report ------------------------------------------------------------

    @action(detail=False, methods=["get"], url_path="tds-report")
    def tds_report(self, request):
        """GET ?from=&to=&view=summary|consultant|detail&basis=paid|period&doctor=&output=csv|xlsx"""
        q = request.query_params
        start, end = _parse_date(q.get("from"), "from"), _parse_date(q.get("to"), "to")
        view = q.get("view", "summary")
        basis = q.get("basis", "paid")
        if view not in ("summary", "consultant", "detail") or basis not in ("paid", "period"):
            raise serializers.ValidationError({"view": "summary | consultant | detail; basis: paid | period"})
        statements = payout_statements.tds_statements(request.user.hospital_id, start, end, basis=basis, doctor=self._doctor(request))
        cols, rows, totals = payout_statements.tds_report(statements, view, basis)
        output = q.get("output")
        if output in ("csv", "xlsx"):
            table = [cols] + [[float(v) if isinstance(v, Decimal) else v for v in r] for r in rows]
            by_col = {"Gross": "gross_payout", "TDS": "tds_amount", "Deductions": "deductions_total", "Net paid": "net_payable"}
            table += [[], [float(totals[by_col[c]]) if c in by_col else ("Total" if i == 0 else "") for i, c in enumerate(cols)]]
            name = f"consultant-tds-{view}-{start}-to-{end}"
            if output == "xlsx":
                from apps.core.xlsx import build_xlsx

                resp = HttpResponse(build_xlsx(table, "TDS"), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                resp["Content-Disposition"] = f'attachment; filename="{name}.xlsx"'
                return resp
            import csv
            import io

            buf = io.StringIO()
            csv.writer(buf).writerows(table)
            resp = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
            resp["Content-Disposition"] = f'attachment; filename="{name}.csv"'
            return resp
        return Response({"from": start, "to": end, "view": view, "basis": basis, "columns": cols, "rows": rows, "totals": totals})


class PayoutStatementLinkView(APIView):
    """The signed link in a WhatsApp statement message (valid 14 days)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, token):
        payout = payout_statements.payout_from_token(token)
        if payout is None:
            return HttpResponse("This link has expired. Please ask the hospital's accounts team for a new statement.", status=404, content_type="text/plain")
        resp = HttpResponse(payout_statements.statement_pdf(payout), content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="{payout_statements.statement_filename(payout)}"'
        return resp
