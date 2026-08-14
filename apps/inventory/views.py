from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import ActionPermissionRequired
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin
from .models import Item, ItemCategory, POItem, PurchaseOrder, StockLevel, StockTransaction
from .serializers import (
    ItemCategorySerializer,
    ItemSerializer,
    POItemSerializer,
    PurchaseOrderSerializer,
    StockLevelSerializer,
    StockTransactionSerializer,
)


class ItemCategoryViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, ActionPermissionRequired]
    action_permissions = {
        "list": "inventory.view_itemcategory",
        "retrieve": "inventory.view_itemcategory",
        "create": "inventory.add_itemcategory",
        "update": "inventory.change_itemcategory",
        "partial_update": "inventory.change_itemcategory",
    }
    serializer_class = ItemCategorySerializer
    queryset = ItemCategory.objects.all()
    audited_fields = ("name", "code")

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)
        self._log("create", serializer.instance)


class ItemViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, ActionPermissionRequired]
    action_permissions = {
        "list": "inventory.view_item",
        "retrieve": "inventory.view_item",
        "create": "inventory.add_item",
        "update": "inventory.change_item",
        "partial_update": "inventory.change_item",
    }
    serializer_class = ItemSerializer
    queryset = Item.objects.all()
    filterset_fields = ["category", "code"]
    audited_fields = ("name", "code", "min_stock_level")

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)
        self._log("create", serializer.instance)


class StockLevelViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, ActionPermissionRequired]
    action_permissions = {
        "list": "inventory.view_stocklevel",
        "retrieve": "inventory.view_stocklevel",
        "create": "inventory.add_stocklevel",
        "update": "inventory.change_stocklevel",
        "partial_update": "inventory.change_stocklevel",
    }
    serializer_class = StockLevelSerializer
    queryset = StockLevel.objects.all()
    filterset_fields = ["item", "batch_number"]
    audited_fields = ("batch_number", "quantity_on_hand", "unit_cost")

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)
        self._log("create", serializer.instance)


class PurchaseOrderViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, ActionPermissionRequired]
    action_permissions = {
        "list": "inventory.view_purchaseorder",
        "retrieve": "inventory.view_purchaseorder",
        "create": "inventory.add_purchaseorder",
        "update": "inventory.change_purchaseorder",
        "partial_update": "inventory.change_purchaseorder",
        "add_item": "inventory.change_purchaseorder",
    }
    serializer_class = PurchaseOrderSerializer
    queryset = PurchaseOrder.objects.all()
    filterset_fields = ["status", "vendor_name"]
    audited_fields = ("po_number", "vendor_name", "status")

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)
        self._log("create", serializer.instance)

    @action(detail=True, methods=["post"], url_path="add-item")
    def add_item(self, request, pk=None):
        po = self.get_object()
        item_id = request.data.get("item")
        qty = request.data.get("ordered_quantity")
        cost = request.data.get("unit_cost")

        if not item_id or not qty or not cost:
            return Response({"error": "item, ordered_quantity, and unit_cost are required"}, status=status.HTTP_400_BAD_REQUEST)

        po_item = POItem.objects.create(
            purchase_order=po,
            item_id=int(item_id),
            ordered_quantity=int(qty),
            unit_cost=cost,
        )

        return Response(POItemSerializer(po_item).data, status=status.HTTP_201_CREATED)


class StockTransactionViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, ActionPermissionRequired]
    action_permissions = {
        "list": "inventory.view_stocktransaction",
        "retrieve": "inventory.view_stocktransaction",
        "create": "inventory.add_stocktransaction",
    }
    serializer_class = StockTransactionSerializer
    queryset = StockTransaction.objects.all()
    filterset_fields = ["item", "transaction_type"]
    audited_fields = ("transaction_type", "quantity", "reference")

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        tx = serializer.save(hospital=hospital)
        self._log("create", tx)
