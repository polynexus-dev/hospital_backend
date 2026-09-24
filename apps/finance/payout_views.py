from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.crud import ADMIN_PERMISSION_CLASSES, TenantCRUDViewSet, model_serializer
from apps.core.permissions import RequiresViewPermission
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin

from . import payouts
from .models_payout import DoctorPayout, DoctorPayoutLine, DoctorPayoutRule


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
    audited_fields = ("doctor", "tariff", "service_department", "basis", "value", "earned_on", "is_active")


LineSerializer = model_serializer(DoctorPayoutLine)


class DoctorPayoutSerializer(serializers.ModelSerializer):
    doctor_name = serializers.CharField(source="doctor.name", read_only=True)
    line_count = serializers.IntegerField(source="lines.count", read_only=True)

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
        for item, _rule, _earned, base, amount in rows:
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
