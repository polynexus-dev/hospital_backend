import csv
import io
from datetime import date, timedelta
from decimal import Decimal

from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Count, Sum
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.crud import TenantCRUDViewSet, model_serializer

from . import accounting
from .models_fpm import (
    ClaimSettlement,
    InsurancePolicy,
    JournalEntry,
    LedgerAccount,
    ServiceTariff,
    SupplierNote,
    Vendor,
    VendorInvoice,
    VendorPayment,
)


def _period(request, default_days=30):
    today = timezone.localdate()
    try:
        start = date.fromisoformat(request.query_params["start"]) if request.query_params.get("start") else today - timedelta(days=default_days)
        end = date.fromisoformat(request.query_params["end"]) if request.query_params.get("end") else today
    except ValueError:
        start, end = today - timedelta(days=default_days), today
    return start, end


class VendorViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(Vendor)
    queryset = Vendor.objects.all()
    search_fields = ["name", "gstin"]
    audited_fields = ("name", "gstin", "credit_days", "bank_account")


class VendorInvoiceSerializer(model_serializer(VendorInvoice, read_only=("total_amount", "status", "match_notes", "approved_by"), extra={
    "vendor_name": serializers.CharField(source="vendor.name", read_only=True),
})):
    paid_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    outstanding = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)


class VendorInvoiceViewSet(TenantCRUDViewSet):
    serializer_class = VendorInvoiceSerializer
    queryset = VendorInvoice.objects.select_related("vendor").prefetch_related("payments")
    filterset_fields = ["vendor", "status", "purchase_order"]
    search_fields = ["invoice_number"]
    audited_fields = ("status", "taxable_amount", "gst_amount", "due_date")

    def perform_create(self, serializer):
        super().perform_create(serializer)
        self._match(serializer.instance)
        accounting.post_vendor_invoice(serializer.instance)

    def _match(self, inv):
        """FPM.2.a 3-way match: invoice value vs PO value vs quantity received on GRNs."""
        po = inv.purchase_order
        if po is None:
            return
        po_value = sum((i.ordered_quantity * i.unit_cost for i in po.po_items.all()), Decimal("0"))
        received_value = sum((i.received_quantity * i.unit_cost for i in po.po_items.all()), Decimal("0"))
        notes = []
        if abs(inv.taxable_amount - received_value) > Decimal("1"):
            notes.append(f"Invoice ₹{inv.taxable_amount} ≠ value received ₹{received_value}")
        if inv.taxable_amount > po_value + Decimal("1"):
            notes.append(f"Invoice exceeds PO value ₹{po_value}")
        inv.status = VendorInvoice.Status.MISMATCH if notes else VendorInvoice.Status.MATCHED
        inv.match_notes = "; ".join(notes)
        inv.save(update_fields=["status", "match_notes"])

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        inv = self.get_object()
        if inv.status == VendorInvoice.Status.MISMATCH and not request.data.get("override_reason"):
            return Response({"detail": "3-way match failed — an override reason is required to approve.", "match_notes": inv.match_notes}, status=400)
        inv.status = VendorInvoice.Status.APPROVED
        inv.approved_by = request.user
        if request.data.get("override_reason"):
            inv.match_notes = f"{inv.match_notes} | Override: {request.data['override_reason']}"
        inv.save()
        return Response(self.get_serializer(inv).data)

    @action(detail=True, methods=["post"])
    def pay(self, request, pk=None):
        inv = self.get_object()
        if inv.status not in (VendorInvoice.Status.APPROVED, VendorInvoice.Status.PARTIALLY_PAID):
            return Response({"detail": "Invoice must be approved before payment."}, status=400)
        amount = Decimal(str(request.data.get("amount") or inv.outstanding))
        tds = Decimal(str(request.data.get("tds_amount") or 0))
        if amount + tds > inv.outstanding + Decimal("0.01"):
            return Response({"amount": f"Exceeds outstanding ₹{inv.outstanding}."}, status=400)
        with transaction.atomic():
            pay = VendorPayment.objects.create(hospital_id=inv.hospital_id, invoice=inv, amount=amount, tds_amount=tds, mode=request.data.get("mode", "neft"),
                                               reference=str(request.data.get("reference", ""))[:60], paid_by=request.user)
            inv.status = VendorInvoice.Status.PAID if inv.outstanding <= Decimal("0.01") else VendorInvoice.Status.PARTIALLY_PAID
            inv.save(update_fields=["status"])
            accounting.post_vendor_payment(pay)
        if inv.vendor.email:  # FPM.2.g supplier payment-status notification
            send_mail(f"Payment released — invoice {inv.invoice_number}",
                      f"Dear {inv.vendor.name},\n\n₹{amount} has been paid against invoice {inv.invoice_number} (ref {pay.reference or '-'})."
                      f"{f' TDS deducted ₹{tds}.' if tds else ''} Outstanding: ₹{inv.outstanding}.\n\n{inv.hospital.name} Accounts",
                      None, [inv.vendor.email], fail_silently=True)
            pay.vendor_notified_at = timezone.now()
            pay.save(update_fields=["vendor_notified_at"])
        return Response(self.get_serializer(inv).data)

    @action(detail=False, methods=["get"])
    def aging(self, request):
        """FPM.2.c/f — payables by vendor and overdue bucket."""
        today = timezone.localdate()
        buckets = defaultdict_float()
        for inv in self.get_queryset().exclude(status=VendorInvoice.Status.PAID):
            out = float(inv.outstanding)
            if out <= 0:
                continue
            days = (today - inv.due_date).days if inv.due_date else 0
            b = "not_due" if days <= 0 else "0_30" if days <= 30 else "31_60" if days <= 60 else "61_90" if days <= 90 else "90_plus"
            buckets[inv.vendor.name][b] += out
            buckets[inv.vendor.name]["total"] += out
        return Response([{"vendor": v, **{k: round(x, 2) for k, x in d.items()}} for v, d in sorted(buckets.items())])

    @action(detail=False, methods=["get"])
    def payment_schedule(self, request):
        """FPM.2.e — what falls due in the next N days per supplier terms."""
        horizon = timezone.localdate() + timedelta(days=int(request.query_params.get("days", 14)))
        qs = self.get_queryset().filter(due_date__lte=horizon, status__in=["approved", "partially_paid", "matched"]).order_by("due_date")
        return Response([{"id": i.pk, "vendor": i.vendor.name, "invoice_number": i.invoice_number, "due_date": i.due_date, "outstanding": float(i.outstanding)} for i in qs])


def defaultdict_float():
    from collections import defaultdict

    return defaultdict(lambda: defaultdict(float))


class SupplierNoteViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(SupplierNote, read_only=("note_number",), extra={"vendor_name": serializers.CharField(source="vendor.name", read_only=True)})
    queryset = SupplierNote.objects.select_related("vendor")
    filterset_fields = ["vendor", "kind"]

    def perform_create(self, serializer):
        super().perform_create(serializer)
        accounting.post_supplier_note(serializer.instance)


class LedgerAccountViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(LedgerAccount, read_only=("is_system",))
    queryset = LedgerAccount.objects.all()
    filterset_fields = ["group"]

    def list(self, request, *args, **kwargs):
        accounting.ensure_accounts(request.user.hospital_id)
        return super().list(request, *args, **kwargs)


class JournalEntryViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = JournalEntry.objects.prefetch_related("lines__account")
    filterset_fields = ["voucher_type", "source_type"]

    class Ser(serializers.ModelSerializer):
        lines = serializers.SerializerMethodField()

        class Meta:
            model = JournalEntry
            exclude = ["hospital"]

        def get_lines(self, obj):
            return [{"account": l.account.name, "debit": float(l.debit), "credit": float(l.credit)} for l in obj.lines.all()]

    serializer_class = Ser

    def get_queryset(self):
        return super().get_queryset().filter(hospital_id=self.request.user.hospital_id)


class TrialBalanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        start, end = _period(request, 365)
        return Response(accounting.trial_balance(request.user.hospital_id, start, end))


class TallyExportView(APIView):
    """GET ?start&end[&masters=1] → Tally Prime XML."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        hospital = request.user.hospital
        if request.query_params.get("masters") == "1":
            xml = accounting.tally_ledger_masters_xml(hospital.pk, hospital.name)
            name = "tally-ledgers.xml"
        else:
            start, end = _period(request)
            entries = JournalEntry.objects.filter(hospital=hospital, entry_date__range=(start, end)).prefetch_related("lines__account")
            xml = accounting.tally_xml(entries, hospital.name)
            entries.update(exported_to_tally_at=timezone.now())
            name = f"tally-vouchers-{start}-{end}.xml"
        resp = HttpResponse(xml, content_type="application/xml")
        resp["Content-Disposition"] = f'attachment; filename="{name}"'
        return resp


class GSTReportView(APIView):
    """GET ?kind=outward|inward&start&end[&export=csv|xlsx]"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        start, end = _period(request)
        kind = request.query_params.get("kind", "outward")
        if kind == "inward":
            rows = accounting.gst_inward_summary(request.user.hospital_id, start, end)
            table = rows
        else:
            data = accounting.gst_outward_summary(request.user.hospital_id, start, end)
            if not request.query_params.get("export"):
                return Response(data)
            table = data["hsn"] if request.query_params.get("section") == "hsn" else data["b2cs"]
        fmt = request.query_params.get("export")
        if not fmt:
            return Response(table)
        headers = list(table[0].keys()) if table else ["no_data"]
        if fmt == "xlsx":
            from apps.core.xlsx import build_xlsx

            body = build_xlsx([headers] + [[r.get(h) if not isinstance(r.get(h), (date,)) else str(r.get(h)) for h in headers] for r in table], "GST")
            ctype, ext = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
        else:
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=headers)
            w.writeheader()
            w.writerows(table)
            body, ctype, ext = buf.getvalue().encode("utf-8-sig"), "text/csv", "csv"
        resp = HttpResponse(body, content_type=ctype)
        resp["Content-Disposition"] = f'attachment; filename="gst-{kind}-{start}-{end}.{ext}"'
        return resp


class ServiceTariffViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(ServiceTariff)
    queryset = ServiceTariff.objects.all()
    filterset_fields = ["department", "is_active"]
    search_fields = ["code", "name"]
    audited_fields = ("rate", "category_rates", "gst_rate", "is_active")


class InsurancePolicyViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(InsurancePolicy, read_only=("eligibility_checked_at",), extra={
        "patient_name": serializers.CharField(source="patient.full_name", read_only=True),
    })
    queryset = InsurancePolicy.objects.select_related("patient")
    filterset_fields = ["patient", "eligibility_status", "tpa_company"]

    @action(detail=True, methods=["post"])
    def verify_eligibility(self, request, pk=None):
        """Records the outcome of the eligibility check (payer portal / NHCX
        CoverageEligibilityRequest once the gateway is live)."""
        p = self.get_object()
        today = timezone.localdate()
        status = request.data.get("status")
        if status not in ("eligible", "ineligible"):
            status = "eligible" if (not p.valid_to or p.valid_to >= today) and (not p.valid_from or p.valid_from <= today) else "ineligible"
        p.eligibility_status = status
        p.eligibility_checked_at = timezone.now()
        if request.data.get("balance_available") is not None:
            p.balance_available = request.data["balance_available"]
        p.save()
        return Response(self.get_serializer(p).data)


class ClaimSettlementViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(ClaimSettlement, read_only=("is_reconciled",))
    queryset = ClaimSettlement.objects.select_related("claim")
    filterset_fields = ["claim", "is_reconciled"]

    def perform_create(self, serializer):
        super().perform_create(serializer)
        s = serializer.instance
        claim = s.claim
        claim.settled_amount = sum((x.amount_received + x.tds_deducted for x in claim.settlements.all()), Decimal("0"))
        claim.status = "settled"
        claim.settled_at = timezone.now()
        claim.save(update_fields=["settled_amount", "status", "settled_at"])
        accounting.post(s.hospital_id, voucher_type="receipt", voucher_number=s.utr_number, source_type="claim_settlement", source_id=s.pk,
                        lines=[("1010", s.amount_received, 0), ("1310", s.tds_deducted, 0), ("5900", s.disallowed_amount, 0),
                               ("1110", 0, s.amount_received + s.tds_deducted + s.disallowed_amount)],
                        narration=f"Claim {claim.claim_number} settlement", party=claim.tpa_company.name)


class PatientStatementView(APIView):
    """FPM.3.f — every bill and payment for a patient with running balance."""

    permission_classes = [IsAuthenticated]

    def get(self, request, patient_id):
        from apps.billing.models import Bill, Payment

        bills = Bill.objects.filter(hospital_id=request.user.hospital_id, patient_id=patient_id).exclude(status="cancelled")
        rows = [{"date": b.created_at, "type": "interim bill" if b.is_interim else "bill", "ref": b.bill_number, "debit": 0 if b.is_interim else float(b.net_amount), "credit": 0} for b in bills]
        rows += [{"date": p.paid_at, "type": f"payment ({p.payment_method})", "ref": p.transaction_id or f"RCPT{p.pk}", "debit": 0, "credit": float(p.amount)}
                 for p in Payment.objects.filter(bill__in=bills)]
        rows.sort(key=lambda r: r["date"])
        bal = 0.0
        for r in rows:
            bal += r["debit"] - r["credit"]
            r["balance"] = round(bal, 2)
        return Response({"entries": rows, "outstanding": round(bal, 2)})


class TPADashboardView(APIView):
    """FPM.4.h — pre-auth and claim pipeline at a glance."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.tpa.models import Claim, PreAuthRequest

        h = request.user.hospital_id
        claims = Claim.objects.filter(hospital_id=h)
        return Response({
            "preauth_by_status": dict(PreAuthRequest.objects.filter(hospital_id=h).values_list("status").annotate(n=Count("id"))),
            "claims_by_status": dict(claims.values_list("status").annotate(n=Count("id"))),
            "billed": float(claims.aggregate(s=Sum("billed_amount"))["s"] or 0),
            "settled": float(claims.aggregate(s=Sum("settled_amount"))["s"] or 0),
            "unreconciled_settlements": ClaimSettlement.objects.filter(hospital_id=h, is_reconciled=False).count(),
            "by_tpa": list(claims.values("tpa_company__name").annotate(n=Count("id"), billed=Sum("billed_amount"), settled=Sum("settled_amount"))),
        })
