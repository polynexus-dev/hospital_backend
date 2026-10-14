"""Vouchers (maker-checker), day book, customer invoicing, receipts, ledger,
ageing and recurring invoices. See finance.vouchers and finance.receivables."""
import csv
import io
from datetime import date, timedelta
from decimal import Decimal

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.crud import ADMIN_PERMISSION_CLASSES, TenantCRUDViewSet, model_serializer
from apps.core.permissions import RequiresViewPermission
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin

from . import receivables, vouchers
from .models_ar import AccountsSettings, Customer, CustomerInvoice, CustomerReceipt, RecurringInvoice
from .models_fpm import JournalEntry


class HasPerm(BasePermission):
    """The view's `required_perm` (a str, or {method: str})."""

    def has_permission(self, request, view):
        perm = getattr(view, "required_perm", None)
        if isinstance(perm, dict):
            perm = perm.get(request.method, perm.get("*"))
        return bool(request.user and request.user.is_authenticated and (not perm or request.user.has_perm(perm)))


BOOKS = "finance.view_journalentry"


def _date(value, name, default=None):
    if not value:
        if default is not None:
            return default
        raise serializers.ValidationError({name: "Required (YYYY-MM-DD)."})
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise serializers.ValidationError({name: "YYYY-MM-DD"})


def _export(table, name, output):
    table = [[float(v) if isinstance(v, Decimal) else ("" if v is None else v) for v in row] for row in table]
    if output == "xlsx":
        from apps.core.xlsx import build_xlsx

        resp = HttpResponse(build_xlsx(table, name[:31]), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="{name}.xlsx"'
        return resp
    buf = io.StringIO()
    csv.writer(buf).writerows(table)
    resp = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{name}.csv"'
    return resp


# --- vouchers -----------------------------------------------------------------------------

class JournalEntrySerializer(serializers.ModelSerializer):
    lines = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    approved_by_name = serializers.SerializerMethodField()

    class Meta:
        model = JournalEntry
        exclude = ["hospital"]

    def get_lines(self, obj):
        return [{"account_code": ln.account.code, "account": ln.account.name, "debit": float(ln.debit), "credit": float(ln.credit)} for ln in obj.lines.all()]

    def get_created_by_name(self, obj):
        return obj.created_by.get_full_name() if obj.created_by_id else ""

    def get_approved_by_name(self, obj):
        return obj.approved_by.get_full_name() if obj.approved_by_id else ""


class JournalEntryViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """All vouchers (read), plus manual vouchers: create, edit while pending
    or rejected, approve / reject (a second person), delete unposted ones."""

    permission_classes = ADMIN_PERMISSION_CLASSES + [RequiresViewPermission]
    serializer_class = JournalEntrySerializer
    queryset = JournalEntry.objects.prefetch_related("lines__account").select_related("created_by", "approved_by")
    filterset_fields = ["voucher_type", "source_type", "status", "is_manual"]
    audited_fields = ("status", "narration", "entry_date", "voucher_number")
    action_permissions = {
        "create": "finance.add_journalentry",
        "partial_update": "finance.change_journalentry",
        "update": "finance.change_journalentry",
        "destroy": "finance.delete_journalentry",
        "approve": "finance.approve_journalentry",
        "reject": "finance.approve_journalentry",
    }

    def _payload(self, request):
        d = request.data
        return {"entry_date": _date(d.get("entry_date"), "entry_date", timezone.localdate()), "narration": str(d.get("narration", "")),
                "party": str(d.get("party_name", d.get("party", ""))), "lines": d.get("lines")}

    def create(self, request):
        p = self._payload(request)
        entry = vouchers.create_manual(request.user.hospital_id, request.user, voucher_type=str(request.data.get("voucher_type", "journal")), **p)
        self._log("create", entry)
        return Response(self.get_serializer(entry).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        entry = self.get_object()
        if entry.created_by_id != request.user.pk:
            return Response({"detail": "Only the person who entered the voucher can change it."}, status=403)
        d = request.data
        entry = vouchers.update_manual(entry, request.user, entry_date=_date(d["entry_date"], "entry_date") if d.get("entry_date") else None,
                                       narration=d.get("narration"), party=d.get("party_name"), lines=d.get("lines"))
        self._log("update", entry)
        return Response(self.get_serializer(entry).data)

    update = partial_update

    def destroy(self, request, pk=None):
        entry = self.get_object()
        if not entry.is_manual or entry.status == JournalEntry.Status.APPROVED:
            return Response({"detail": "Posted vouchers can't be deleted; pass a reversing voucher instead."}, status=400)
        self._log("delete", entry)
        entry.delete()
        return Response(status=204)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        entry = vouchers.approve(self.get_object(), request.user)
        self._log("update", entry)
        return Response(self.get_serializer(entry).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        entry = vouchers.reject(self.get_object(), request.user, str(request.data.get("reason", "")).strip())
        self._log("update", entry)
        return Response(self.get_serializer(entry).data)


class DayBookView(APIView):
    """GET ?start&end&voucher_type&account&party&search&status=approved|pending|rejected|all
    &page&page_size&output=csv|xlsx — vouchers with their lines, page and grand totals."""

    permission_classes = [IsAuthenticated, HasPerm]
    required_perm = BOOKS

    def get(self, request):
        q = request.query_params
        today = timezone.localdate()
        start, end = _date(q.get("start"), "start", today), _date(q.get("end"), "end", today)
        if start > end:
            raise serializers.ValidationError({"end": "Must be on or after start."})
        qs, totals = vouchers.day_book(request.user.hospital_id, start=start, end=end, voucher_type=q.get("voucher_type", ""), account=q.get("account", ""),
                                       party=q.get("party", ""), status=q.get("status", "approved"), search=q.get("search", ""))
        qs = qs.prefetch_related("lines__account").select_related("created_by", "approved_by")
        if q.get("output") in ("csv", "xlsx"):
            table = [["Date", "Voucher type", "Voucher no.", "Party", "Narration", "Ledger", "Debit", "Credit", "Status"]]
            for e in qs:
                for i, ln in enumerate(e.lines.all()):
                    first = i == 0
                    table.append([str(e.entry_date) if first else "", e.get_voucher_type_display() if first else "", e.voucher_number if first else "",
                                  e.party_name if first else "", e.narration if first else "", f"{ln.account.code} {ln.account.name}", ln.debit, ln.credit,
                                  e.get_status_display() if first else ""])
            table += [[], ["", "", "", "", "", "Total", totals["debit"], totals["credit"], ""]]
            return _export(table, f"day-book-{start}-to-{end}", q["output"])
        try:
            page, size = max(int(q.get("page", 1)), 1), min(max(int(q.get("page_size", 50)), 1), 200)
        except ValueError:
            page, size = 1, 50
        count = totals["vouchers"]
        rows = [vouchers.entry_row(e) for e in qs[(page - 1) * size: page * size]]
        return Response({
            "start": start, "end": end, "count": count, "page": page, "page_size": size, "pages": max((count + size - 1) // size, 1),
            "results": rows,
            "page_totals": {"debit": sum((r["debit"] for r in rows), Decimal("0")), "credit": sum((r["credit"] for r in rows), Decimal("0"))},
            "totals": totals,
        })


# --- accounts settings ---------------------------------------------------------------------

class AccountsSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccountsSettings
        exclude = ["hospital", "id"]

    def validate_gstin(self, v):
        import re

        v = (v or "").strip().upper()
        if v and not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", v):
            raise serializers.ValidationError("GSTIN looks like 27ABCDE1234F1Z5.")
        return v


class AccountsSettingsView(APIView):
    permission_classes = [IsAuthenticated, HasPerm]
    required_perm = {"GET": "finance.view_accountssettings", "PUT": "finance.change_accountssettings"}

    def get(self, request):
        return Response(AccountsSettingsSerializer(AccountsSettings.for_hospital(request.user.hospital_id)).data)

    def put(self, request):
        s = AccountsSettings.for_hospital(request.user.hospital_id)
        ser = AccountsSettingsSerializer(s, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        if s.gstin and not s.state_code:
            s.state_code = s.gstin[:2]
            s.save(update_fields=["state_code"])
        return Response(AccountsSettingsSerializer(s).data)


# --- customers ----------------------------------------------------------------------------------

class CustomerViewSet(TenantCRUDViewSet):
    permission_classes = TenantCRUDViewSet.permission_classes + [RequiresViewPermission]
    serializer_class = model_serializer(Customer)
    queryset = Customer.objects.all()
    filterset_fields = ["kind", "is_active"]
    search_fields = ["name", "gstin", "contact_person"]
    audited_fields = ("name", "gstin", "state_code", "credit_days", "is_active")
    action_permissions = {"ledger": "finance.view_customer", "ageing": "finance.view_customerinvoice"}

    def perform_create(self, serializer):
        gstin = (serializer.validated_data.get("gstin") or "").upper()
        extra = {"gstin": gstin}
        if gstin and not serializer.validated_data.get("state_code"):
            extra["state_code"] = gstin[:2]
        serializer.validated_data.update(extra)
        super().perform_create(serializer)

    @action(detail=True, methods=["get"])
    def ledger(self, request, pk=None):
        """GET ?start&end&output=csv|xlsx"""
        customer = self.get_object()
        today = timezone.localdate()
        start = _date(request.query_params.get("start"), "start", date(today.year if today.month >= 4 else today.year - 1, 4, 1))
        end = _date(request.query_params.get("end"), "end", today)
        data = receivables.customer_ledger(customer, start, end)
        if request.query_params.get("output") in ("csv", "xlsx"):
            table = [[f"Ledger: {customer.name}", f"{start} to {end}"], ["Date", "Particulars", "Debit", "Credit", "Balance"],
                     ["", "Opening balance", "", "", data["opening"]]]
            table += [[str(r["date"]), r["particulars"], r["debit"] or "", r["credit"] or "", r["balance"]] for r in data["rows"]]
            table += [["", "Closing balance", data["debit"], data["credit"], data["closing"]]]
            return _export(table, f"ledger-{customer.pk}-{start}-to-{end}", request.query_params["output"])
        return Response(data)

    @action(detail=False, methods=["get"])
    def ageing(self, request):
        """GET ?as_of&output=csv|xlsx — outstanding per customer by days past due."""
        data = receivables.ageing(request.user.hospital_id, _date(request.query_params.get("as_of"), "as_of", timezone.localdate()))
        if request.query_params.get("output") in ("csv", "xlsx"):
            keys = ["current", "1_30", "31_60", "61_90", "over_90", "on_account", "total"]
            table = [["Customer", "Not yet due", "1-30", "31-60", "61-90", "Over 90", "On account", "Net outstanding"]]
            table += [[r["customer_name"]] + [r[k] for k in keys] for r in data["rows"]]
            table += [["Total"] + [data["totals"][k] for k in keys]]
            return _export(table, f"customer-ageing-{data['as_of']}", request.query_params["output"])
        return Response(data)


# --- invoices -------------------------------------------------------------------------------------

class InvoiceLineSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    tariff = serializers.IntegerField(source="tariff_id", allow_null=True)
    description = serializers.CharField()
    hsn_sac = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2)
    rate = serializers.DecimalField(max_digits=12, decimal_places=2)
    discount = serializers.DecimalField(max_digits=12, decimal_places=2)
    gst_rate = serializers.DecimalField(max_digits=5, decimal_places=2)
    taxable_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    tax_amount = serializers.DecimalField(max_digits=14, decimal_places=2)


class CustomerInvoiceSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    lines = InvoiceLineSerializer(many=True, read_only=True)
    outstanding = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    recurring_name = serializers.CharField(source="recurring.name", read_only=True, default="")

    class Meta:
        model = CustomerInvoice
        exclude = ["hospital"]


class CustomerInvoiceViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """Drafts are edited freely; issuing numbers the invoice and posts it.
    An issued invoice is never edited — cancel it (credit note) instead."""

    permission_classes = ADMIN_PERMISSION_CLASSES + [RequiresViewPermission]
    serializer_class = CustomerInvoiceSerializer
    queryset = CustomerInvoice.objects.select_related("customer", "recurring").prefetch_related("lines")
    filterset_fields = ["customer", "status", "recurring"]
    search_fields = ["invoice_number", "customer__name"]
    audited_fields = ("status", "total_amount", "invoice_number", "cancelled_reason")
    action_permissions = {
        "issue": "finance.change_customerinvoice", "cancel": "finance.change_customerinvoice",
        "email": "finance.change_customerinvoice", "pdf": "finance.view_customerinvoice", "receive": "finance.add_customerreceipt",
    }

    def _save(self, request, invoice=None):
        d = request.data
        customer = receivables.customer_for(request.user.hospital_id, d.get("customer") or (invoice and invoice.customer_id))
        return receivables.save_draft(
            request.user.hospital_id, request.user, customer=customer, lines=d.get("lines") if "lines" in d else self._lines_of(invoice),
            invoice=invoice, invoice_date=_date(d.get("invoice_date"), "invoice_date", invoice.invoice_date if invoice else timezone.localdate()),
            due_date=_date(d["due_date"], "due_date") if d.get("due_date") else None, notes=str(d.get("notes", invoice.notes if invoice else "")),
        )

    @staticmethod
    def _lines_of(invoice):
        if invoice is None:
            return None
        return [{"tariff": ln.tariff_id, "description": ln.description, "hsn_sac": ln.hsn_sac, "quantity": ln.quantity, "rate": ln.rate,
                 "discount": ln.discount, "gst_rate": ln.gst_rate} for ln in invoice.lines.all()]

    def create(self, request, *args, **kwargs):
        inv = self._save(request)
        if request.data.get("issue"):
            receivables.issue(inv)
        self._log("create", inv)
        return Response(self.get_serializer(self.get_queryset().get(pk=inv.pk)).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        inv = self._save(request, self.get_object())
        self._log("update", inv)
        return Response(self.get_serializer(self.get_queryset().get(pk=inv.pk)).data)

    partial_update = update

    def destroy(self, request, *args, **kwargs):
        inv = self.get_object()
        if inv.status != CustomerInvoice.Status.DRAFT:
            return Response({"detail": "Only a draft can be deleted; cancel an issued invoice."}, status=400)
        self._log("delete", inv)
        inv.delete()
        return Response(status=204)

    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        inv = receivables.issue(self.get_object())
        self._log("update", inv)
        if request.data.get("email") and inv.customer.email:
            receivables.email_invoice(inv)
        return Response(self.get_serializer(self.get_queryset().get(pk=inv.pk)).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        inv = receivables.cancel(self.get_object(), str(request.data.get("reason", "")).strip())
        self._log("update", inv)
        return Response(self.get_serializer(inv).data)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        inv = self.get_object()
        resp = HttpResponse(receivables.invoice_pdf(inv), content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="Invoice_{(inv.invoice_number or f"draft-{inv.pk}").replace("/", "-")}.pdf"'
        return resp

    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        """POST {amount, tds_amount?, mode, reference, received_on?} — a receipt against this invoice."""
        inv = self.get_object()
        d = request.data
        try:
            amount, tds = Decimal(str(d.get("amount"))), Decimal(str(d.get("tds_amount") or 0))
        except Exception:  # noqa: BLE001
            raise serializers.ValidationError({"amount": "A number."})
        r = receivables.record_receipt(request.user.hospital_id, request.user, customer=inv.customer, invoice=inv, amount=amount, tds_amount=tds,
                                       mode=str(d.get("mode", "neft")), reference=str(d.get("reference", "")).strip(),
                                       received_on=_date(d.get("received_on"), "received_on", timezone.localdate()))
        self._log("create", r)
        return Response(self.get_serializer(self.get_queryset().get(pk=inv.pk)).data)

    @action(detail=True, methods=["post"])
    def email(self, request, pk=None):
        to = receivables.email_invoice(self.get_object(), to=str(request.data.get("to", "")).strip() or None)
        return Response({"sent_to": to})


class CustomerReceiptSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    invoice_number = serializers.CharField(source="invoice.invoice_number", read_only=True, default="")

    class Meta:
        model = CustomerReceipt
        exclude = ["hospital"]
        read_only_fields = ["received_by"]


class CustomerReceiptViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                             mixins.CreateModelMixin, viewsets.GenericViewSet):
    """Money received from customers, against an invoice or on account. Posted at once; never edited."""

    permission_classes = ADMIN_PERMISSION_CLASSES + [RequiresViewPermission]
    serializer_class = CustomerReceiptSerializer
    queryset = CustomerReceipt.objects.select_related("customer", "invoice")
    filterset_fields = ["customer", "invoice", "mode"]

    def create(self, request, *args, **kwargs):
        d = request.data
        customer = receivables.customer_for(request.user.hospital_id, d.get("customer"))
        invoice = None
        if d.get("invoice"):
            invoice = CustomerInvoice.objects.filter(pk=d["invoice"], hospital_id=request.user.hospital_id).first()
            if invoice is None:
                raise serializers.ValidationError({"invoice": "Not found."})
        try:
            amount, tds = Decimal(str(d.get("amount"))), Decimal(str(d.get("tds_amount") or 0))
        except Exception:  # noqa: BLE001
            raise serializers.ValidationError({"amount": "A number."})
        r = receivables.record_receipt(request.user.hospital_id, request.user, customer=customer, invoice=invoice, amount=amount, tds_amount=tds,
                                       mode=str(d.get("mode", "neft")), reference=str(d.get("reference", "")).strip(),
                                       received_on=_date(d.get("received_on"), "received_on", timezone.localdate()), notes=str(d.get("notes", "")))
        self._log("create", r)
        return Response(self.get_serializer(r).data, status=status.HTTP_201_CREATED)


# --- recurring --------------------------------------------------------------------------------------

class RecurringInvoiceSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    invoice_count = serializers.IntegerField(source="invoices.count", read_only=True)

    class Meta:
        model = RecurringInvoice
        exclude = ["hospital"]
        read_only_fields = ["last_run_at", "created_by", "next_run_date"]

    def validate(self, attrs):
        request = self.context["request"]
        customer = attrs.get("customer") or getattr(self.instance, "customer", None)
        if customer is not None and customer.hospital_id != request.user.hospital_id:
            raise serializers.ValidationError({"customer": "Not found."})
        if "lines" in attrs:
            receivables._clean_lines(request.user.hospital_id, attrs["lines"])  # raises on bad lines
        start = attrs.get("start_date") or getattr(self.instance, "start_date", None)
        end = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start and end and end < start:
            raise serializers.ValidationError({"end_date": "Must be after the start date."})
        return attrs


class RecurringInvoiceViewSet(TenantCRUDViewSet):
    permission_classes = TenantCRUDViewSet.permission_classes + [RequiresViewPermission]
    serializer_class = RecurringInvoiceSerializer
    queryset = RecurringInvoice.objects.select_related("customer")
    filterset_fields = ["customer", "is_active", "frequency"]
    audited_fields = ("customer", "frequency", "lines", "start_date", "end_date", "auto_issue", "is_active")
    action_permissions = {"run_now": "finance.add_customerinvoice"}

    def perform_create(self, serializer):
        serializer.validated_data["next_run_date"] = serializer.validated_data["start_date"]
        serializer.validated_data["created_by"] = self.request.user
        super().perform_create(serializer)

    def perform_update(self, serializer):
        rec = serializer.instance
        new_start = serializer.validated_data.get("start_date")
        if new_start and new_start != rec.start_date and not rec.invoices.exists():
            serializer.validated_data["next_run_date"] = new_start
        super().perform_update(serializer)

    @action(detail=False, methods=["post"], url_path="run-now")
    def run_now(self, request):
        """Raise every invoice that's due today (the nightly job does this too)."""
        made = receivables.run_recurring()
        return Response({"invoices_raised": made})
