"""NABH FPM.1 procurement: approval rules, store indents, GRN with
discrepancy flags, inter-store transfers, supplier quality ratings."""
from decimal import Decimal

from django.db import transaction
from django.db.models import Avg, F
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.core.crud import TenantCRUDViewSet, model_serializer

from .models import (
    GoodsReceiptNote,
    Item,
    POItem,
    PurchaseApprovalRule,
    PurchaseOrder,
    StockLevel,
    StockTransaction,
    StockTransfer,
    Store,
    StoreIndent,
    SupplierRating,
)


class StoreViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(Store)
    queryset = Store.objects.all()


class PurchaseApprovalRuleViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(PurchaseApprovalRule)
    queryset = PurchaseApprovalRule.objects.all()


def po_value(po):
    return sum((i.ordered_quantity * i.unit_cost for i in po.po_items.all()), Decimal("0"))


class PurchaseOrderApprovalMixin:
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """FPM.1.a — the approver's role must match the rule for this PO's value."""
        po = self.get_object()
        value = po_value(po)
        rule = PurchaseApprovalRule.objects.filter(hospital_id=po.hospital_id, min_amount__lte=value).filter(
            models_q_max(value)).order_by("-min_amount").first()
        role = getattr(getattr(request.user, "role", None), "template", "")
        if rule and role != rule.approver_role and not request.user.is_superuser and role not in ("owner", "admin"):
            return Response({"detail": f"PO value ₹{value} needs approval by {rule.approver_role}."}, status=403)
        po.approved_by = request.user
        po.approved_at = timezone.now()
        po.save(update_fields=["approved_by", "approved_at"])
        return Response({"id": po.pk, "approved_by": request.user.email, "value": float(value)})


def models_q_max(value):
    from django.db.models import Q

    return Q(max_amount__isnull=True) | Q(max_amount__gte=value)


class StoreIndentViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(StoreIndent, read_only=("indent_number", "status", "requested_by", "approved_by"))
    queryset = StoreIndent.objects.all()
    filterset_fields = ["status", "department"]
    actor_field = "requested_by"

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        ind = self.get_object()
        ind.status = StoreIndent.Status.APPROVED
        ind.approved_by = request.user
        ind.save()
        return Response(self.get_serializer(ind).data)

    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        ind = self.get_object()
        if ind.status != StoreIndent.Status.APPROVED:
            return Response({"detail": "Indent must be approved first."}, status=400)
        with transaction.atomic():
            for line in ind.items:
                need = int(line.get("quantity", 0))
                for lvl in StockLevel.objects.select_for_update().filter(hospital_id=ind.hospital_id, item_id=line.get("item"), quantity_on_hand__gt=0).order_by("expiry_date"):
                    take = min(need, lvl.quantity_on_hand)
                    lvl.quantity_on_hand -= take
                    lvl.save(update_fields=["quantity_on_hand"])
                    need -= take
                    if need <= 0:
                        break
                issued = int(line.get("quantity", 0)) - need
                line["issued_quantity"] = issued
                if issued:
                    StockTransaction.objects.create(hospital_id=ind.hospital_id, item_id=line["item"], transaction_type="issue", quantity=-issued, reference=ind.indent_number)
            ind.status = StoreIndent.Status.ISSUED
            ind.save()
        return Response(self.get_serializer(ind).data)


class GoodsReceiptNoteViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(GoodsReceiptNote, read_only=("grn_number", "has_discrepancy", "discrepancy_notes", "received_by"), extra={
        "po_number": serializers.CharField(source="purchase_order.po_number", read_only=True),
    })
    queryset = GoodsReceiptNote.objects.select_related("purchase_order")
    filterset_fields = ["purchase_order", "has_discrepancy"]
    actor_field = "received_by"

    def perform_create(self, serializer):
        """Posts received stock, updates PO received quantities, and flags
        short/excess/rejected quantities against the PO (FPM.1.e)."""
        po = serializer.validated_data["purchase_order"]
        with transaction.atomic():
            super().perform_create(serializer)
            grn = serializer.instance
            issues = []
            for line in grn.items:
                poi = POItem.objects.select_for_update().filter(pk=line.get("po_item"), purchase_order=po).first()
                if poi is None:
                    raise ValidationError({"items": f"PO item {line.get('po_item')} is not on this PO."})
                got = int(line.get("received_quantity", 0))
                rejected = int(line.get("rejected_quantity", 0))
                accepted = got - rejected
                pending = poi.ordered_quantity - poi.received_quantity
                if got > pending:
                    issues.append(f"{poi.item.name}: excess {got - pending}")
                elif got < pending:
                    issues.append(f"{poi.item.name}: short {pending - got}")
                if rejected:
                    issues.append(f"{poi.item.name}: {rejected} rejected ({line.get('remarks', 'quality')})")
                poi.received_quantity = F("received_quantity") + accepted
                poi.save(update_fields=["received_quantity"])
                if accepted:
                    StockLevel.objects.create(hospital_id=grn.hospital_id, item=poi.item, batch_number=line.get("batch_number") or grn.grn_number,
                                              expiry_date=line.get("expiry_date") or None, quantity_on_hand=accepted, unit_cost=poi.unit_cost)
                    StockTransaction.objects.create(hospital_id=grn.hospital_id, item=poi.item, transaction_type="receipt", quantity=accepted, reference=grn.grn_number)
            grn.has_discrepancy = bool(issues)
            grn.discrepancy_notes = "; ".join(issues)
            grn.save(update_fields=["has_discrepancy", "discrepancy_notes"])
            po.refresh_from_db()
            if all(i.received_quantity >= i.ordered_quantity for i in po.po_items.all()):
                po.status = "received"
                po.save(update_fields=["status"])


class StockTransferViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(StockTransfer, read_only=("transferred_by",), extra={"item_name": serializers.CharField(source="item.name", read_only=True)})
    queryset = StockTransfer.objects.select_related("item")
    filterset_fields = ["item", "from_store", "to_store"]
    actor_field = "transferred_by"

    def perform_create(self, serializer):
        v = serializer.validated_data
        if v["from_store"] == v["to_store"]:
            raise ValidationError({"to_store": "Choose a different destination store."})
        super().perform_create(serializer)
        t = serializer.instance
        StockTransaction.objects.create(hospital_id=t.hospital_id, item=t.item, transaction_type="transfer", quantity=0,
                                        reference=f"{t.from_store.code}→{t.to_store.code} x{t.quantity}")


class SupplierRatingViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(SupplierRating, read_only=("rated_by",))
    queryset = SupplierRating.objects.all()
    filterset_fields = ["vendor_name"]
    actor_field = "rated_by"

    def perform_create(self, serializer):
        for f in ("quality", "delivery_timeliness", "packaging"):
            if not 1 <= int(serializer.validated_data.get(f, 3)) <= 5:
                raise ValidationError({f: "1–5"})
        super().perform_create(serializer)

    @action(detail=False, methods=["get"])
    def scorecard(self, request):
        rows = self.get_queryset().values("vendor_name").annotate(quality=Avg("quality"), delivery=Avg("delivery_timeliness"), packaging=Avg("packaging")).order_by("-quality")
        return Response([{k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()} for r in rows])
