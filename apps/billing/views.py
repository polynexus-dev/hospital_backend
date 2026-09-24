from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import ActionPermissionRequired
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin
from .models import Bill, BillItem, InsuranceClaim, Payment
from .serializers import BillItemSerializer, BillSerializer, InsuranceClaimSerializer, PaymentSerializer


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
    }
    serializer_class = BillSerializer
    queryset = Bill.objects.all()
    filterset_fields = ["patient", "admission", "status"]
    audited_fields = ("total_amount", "net_amount", "status")

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)
        self._log("create", serializer.instance)

    @action(detail=True, methods=["post"], url_path="add-item")
    def add_item(self, request, pk=None):
        bill = self.get_object()
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

        item = BillItem.objects.create(
            bill=bill,
            description=desc,
            quantity=int(qty),
            unit_price=price,
            total_price=0,
            **extra,
        )

        # Recalculate bill total (GST shown separately and added to net)
        from decimal import Decimal

        items = list(bill.items.all())
        total = sum((i.total_price for i in items), Decimal("0"))
        tax = sum((i.tax_amount for i in items), Decimal("0"))
        bill.total_amount = total
        bill.tax_amount = tax
        bill.net_amount = total + tax - Decimal(bill.discount_amount or 0)
        bill.save(update_fields=["total_amount", "tax_amount", "net_amount"])

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
        hospital = getattr(self.request.user, "hospital", None)
        payment = serializer.save(hospital=hospital)
        self._log("create", payment)

        # Update bill status if fully paid
        bill = payment.bill
        total_paid = sum(p.amount for p in bill.payments.all())
        if total_paid >= bill.net_amount:
            bill.status = Bill.Status.PAID
        else:
            bill.status = Bill.Status.PARTIALLY_PAID
        bill.save(update_fields=["status"])


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
    running = Bill.objects.filter(admission=adm, is_interim=False).exclude(status="cancelled")
    interim = Bill.objects.create(hospital_id=adm.hospital_id, patient=adm.patient, admission=adm, is_interim=True, status="draft")
    for item in BillItem.objects.filter(bill__in=running):
        BillItem.objects.create(bill=interim, description=item.description, quantity=item.quantity, unit_price=item.unit_price, total_price=0,
                                hsn_sac=item.hsn_sac, gst_rate=item.gst_rate, tariff=item.tariff)
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
