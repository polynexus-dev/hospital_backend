from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import ActionPermissionRequired
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin
from .models import Item, ItemCategory, POItem, PurchaseOrder, StockLevel, StockTransaction
from .procurement import PurchaseOrderApprovalMixin
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
    # select_related: ItemSerializer.category_name (source="category.name")
    queryset = Item.objects.select_related("category")
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
    # select_related: StockLevelSerializer.item_name (source="item.name")
    queryset = StockLevel.objects.select_related("item")
    filterset_fields = ["item", "batch_number"]
    audited_fields = ("batch_number", "quantity_on_hand", "unit_cost")

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)
        self._log("create", serializer.instance)


class PurchaseOrderViewSet(PurchaseOrderApprovalMixin, AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, ActionPermissionRequired]
    action_permissions = {
        "list": "inventory.view_purchaseorder",
        "retrieve": "inventory.view_purchaseorder",
        "create": "inventory.add_purchaseorder",
        "update": "inventory.change_purchaseorder",
        "partial_update": "inventory.change_purchaseorder",
        "add_item": "inventory.change_purchaseorder",
        "approve": "inventory.change_purchaseorder",
        "schedule": "inventory.change_purchaseorder",
        "deliveries": "inventory.view_purchaseorder",
    }
    serializer_class = PurchaseOrderSerializer
    # prefetch_related: PurchaseOrderSerializer.po_items (reverse FK, many=True),
    # whose own POItemSerializer.item_name needs item joined too.
    queryset = PurchaseOrder.objects.select_related("approved_by").prefetch_related("po_items__item", "po_items__deliveries", "po_items__contract_line__contract")
    filterset_fields = ["status", "vendor_name"]
    audited_fields = ("po_number", "vendor_name", "status")

    def get_queryset(self):
        from .contracts import po_filters

        qs = super().get_queryset()
        return po_filters(qs, self.request.query_params) if self.action == "list" else qs

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)
        self._log("create", serializer.instance)

    @action(detail=True, methods=["post"], url_path="add-item")
    def add_item(self, request, pk=None):
        """Adds a line. A blank unit_cost takes the vendor's rate-contract rate;
        a cost above the contract rate needs rate_override_reason."""
        from .contracts import add_po_line

        po = self.get_object()
        item = Item.objects.filter(hospital_id=po.hospital_id, pk=request.data.get("item") or 0).first()
        qty = request.data.get("ordered_quantity")
        if item is None or not qty:
            return Response({"error": "item and ordered_quantity are required"}, status=status.HTTP_400_BAD_REQUEST)
        po_item = add_po_line(po, item, qty, request.data.get("unit_cost"), request.data.get("rate_override_reason", ""))
        return Response(POItemSerializer(po_item).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def schedule(self, request, pk=None):
        """POST {po_item, deliveries: [{due_date, quantity}]} — the line's delivery schedule (replaces any earlier one)."""
        from .contracts import set_schedule

        po = self.get_object()
        po_item = po.po_items.filter(pk=request.data.get("po_item") or 0).first()
        if po_item is None:
            return Response({"po_item": "Not a line on this PO."}, status=status.HTTP_400_BAD_REQUEST)
        set_schedule(po_item, request.data.get("deliveries") or [])
        return Response(self.get_serializer(self.get_queryset().get(pk=po.pk)).data)

    @action(detail=False, methods=["get"])
    def deliveries(self, request):
        """GET ?until=YYYY-MM-DD&overdue=1 — scheduled deliveries still pending on open POs."""
        from django.utils import timezone
        from django.utils.dateparse import parse_date

        from .contracts import schedule_rows

        until = parse_date(request.query_params.get("until") or "") or timezone.localdate() + timezone.timedelta(days=7)
        items = POItem.objects.filter(purchase_order__in=super().get_queryset().exclude(status__in=["received", "cancelled"])).select_related(
            "purchase_order", "item").prefetch_related("deliveries")
        rows = [r for r in schedule_rows(items) if r["pending"] > 0 and r["due_date"] <= until]
        if request.query_params.get("overdue") in ("1", "true"):
            rows = [r for r in rows if r["overdue"]]
        return Response(sorted(rows, key=lambda r: (r["due_date"], r["po_number"])))


class StockTransactionViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, ActionPermissionRequired]
    action_permissions = {
        "list": "inventory.view_stocktransaction",
        "retrieve": "inventory.view_stocktransaction",
        "create": "inventory.add_stocktransaction",
    }
    serializer_class = StockTransactionSerializer
    # select_related: StockTransactionSerializer.item_name (source="item.name")
    queryset = StockTransaction.objects.select_related("item")
    filterset_fields = ["item", "transaction_type"]
    audited_fields = ("transaction_type", "quantity", "reference")

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        tx = serializer.save(hospital=hospital)
        self._log("create", tx)
