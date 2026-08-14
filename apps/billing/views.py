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

        if not desc or not price:
            return Response({"error": "description and unit_price are required"}, status=status.HTTP_400_BAD_REQUEST)

        item = BillItem.objects.create(
            bill=bill,
            description=desc,
            quantity=int(qty),
            unit_price=price,
            total_price=int(qty) * float(price),
        )

        # Recalculate bill total
        total = sum(i.total_price for i in bill.items.all())
        bill.total_amount = total
        bill.net_amount = float(total) - float(bill.discount_amount)
        bill.save(update_fields=["total_amount", "net_amount"])

        return Response(BillItemSerializer(item).data, status=status.HTTP_201_CREATED)


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
