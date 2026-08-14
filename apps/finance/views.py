from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.core.permissions import ActionPermissionRequired
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin
from .models import Expense, Ledger, Receivable
from .serializers import ExpenseSerializer, LedgerSerializer, ReceivableSerializer


class LedgerViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.IsAuthenticated, ActionPermissionRequired]
    action_permissions = {
        "list": "finance.view_ledger",
        "retrieve": "finance.view_ledger",
    }
    serializer_class = LedgerSerializer

    def get_queryset(self):
        return Ledger.objects.filter(hospital=self.request.user.hospital)


class ExpenseViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, ActionPermissionRequired]
    action_permissions = {
        "list": "finance.view_expense",
        "retrieve": "finance.view_expense",
        "create": "finance.add_expense",
        "update": "finance.change_expense",
        "partial_update": "finance.change_expense",
        "approve": "finance.change_expense",
    }
    serializer_class = ExpenseSerializer
    audited_fields = ("category", "amount", "paid_to", "approved_by")

    def get_queryset(self):
        return Expense.objects.filter(hospital=self.request.user.hospital)

    def perform_create(self, serializer):
        expense = serializer.save(hospital=self.request.user.hospital, paid_by=self.request.user)
        self._log("create", expense)
        # Automatically post expense to Ledger
        Ledger.objects.create(
            hospital=expense.hospital,
            entry_type=Ledger.EntryType.EXPENSE,
            category=expense.category,
            amount=expense.amount,
            reference_type="Expense",
            reference_id=str(expense.id),
        )

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        expense = self.get_object()
        expense.approved_by = request.user
        expense.save()
        self._log("update", expense)
        return Response(ExpenseSerializer(expense).data)


class ReceivableViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, ActionPermissionRequired]
    action_permissions = {
        "list": "finance.view_receivable",
        "retrieve": "finance.view_receivable",
        "create": "finance.add_receivable",
        "update": "finance.change_receivable",
        "partial_update": "finance.change_receivable",
    }
    serializer_class = ReceivableSerializer
    audited_fields = ("source_type", "amount", "status")

    def get_queryset(self):
        return Receivable.objects.filter(hospital=self.request.user.hospital)

    def perform_create(self, serializer):
        rec = serializer.save(hospital=self.request.user.hospital)
        self._log("create", rec)
