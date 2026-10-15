"""Inventory consumption (MedNet M2.22 procurement): per item, what was
consumed and received over the last N days, stock in hand, safety stock and
the quantity to request so stock lasts the chosen number of days — filtered
by one or more categories / sub-categories — and a transfer request (store
indent) raised straight from the screen. Also raising a PO from an approved
indent, at most one live PO per indent."""
from collections import defaultdict
from datetime import timedelta
from math import ceil

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Item, ItemCategory, PurchaseOrder, StockLevel, StockTransaction, Store, StoreIndent

OPEN_INDENT = (StoreIndent.Status.REQUESTED, StoreIndent.Status.APPROVED)


def _ids(raw):
    return [int(x) for x in str(raw or "").split(",") if x.strip().isdigit()]


def consumption_rows(hospital_id, *, categories=(), subcategories=(), days=7, duration=10, below_only=False, search=""):
    items = Item.objects.filter(hospital_id=hospital_id).select_related("category__parent")
    if subcategories:
        items = items.filter(category_id__in=subcategories)
    elif categories:
        items = items.filter(Q(category_id__in=categories) | Q(category__parent_id__in=categories))
    if search:
        items = items.filter(Q(name__icontains=search) | Q(code__icontains=search))
    items = list(items.order_by("name")[:2000])
    ids = [i.pk for i in items]
    since = timezone.now() - timedelta(days=days)
    tx = defaultdict(lambda: {"issue": 0, "receipt": 0})
    moves = (StockTransaction.objects.filter(hospital_id=hospital_id, item_id__in=ids, transaction_date__gte=since, transaction_type__in=["issue", "receipt"])
             .values("item_id", "transaction_type").annotate(q=Sum("quantity")))
    for row in moves:
        tx[row["item_id"]][row["transaction_type"]] = abs(row["q"] or 0)
    on_hand = dict(StockLevel.objects.filter(hospital_id=hospital_id, item_id__in=ids).values("item_id").annotate(q=Sum("quantity_on_hand")).values_list("item_id", "q"))
    requested = defaultdict(int)
    for ind in StoreIndent.objects.filter(hospital_id=hospital_id, status__in=OPEN_INDENT).only("items"):
        for line in ind.items or []:
            try:
                requested[int(line.get("item"))] += int(line.get("quantity", 0)) - int(line.get("issued_quantity", 0) or 0)
            except (TypeError, ValueError, AttributeError):
                continue
    rows = []
    for i in items:
        used, inward, hand = tx[i.pk]["issue"], tx[i.pk]["receipt"], int(on_hand.get(i.pk) or 0)
        daily = used / days if days else 0
        need = ceil(daily * duration) + i.safety_stock  # what should be in hand to last `duration` days
        roq = max(0, need - hand)
        already = max(0, requested.get(i.pk, 0))
        below = hand < max(need, i.min_stock_level)
        if below_only and not below:
            continue
        cat = i.category
        rows.append({
            "item": i.pk, "code": i.code, "name": i.name, "unit": i.unit_of_measure,
            "category": cat.parent.name if cat.parent_id else cat.name, "subcategory": cat.name if cat.parent_id else "",
            "consumption": used, "inward": inward, "stock_in_hand": hand, "avg_daily": round(daily, 2),
            "safety_stock": i.safety_stock, "reorder_level": i.min_stock_level, "roq": roq,
            "already_requested": already, "trq": max(0, roq - already), "below": below,
        })
    return rows


def _int(params, key, default, lo, hi):
    try:
        return min(max(int(params.get(key, default)), lo), hi)
    except (TypeError, ValueError):
        return default


class InventoryConsumptionView(APIView):
    """GET /inventory/consumption/?category=1,2&subcategory=3&days=7&duration=10&below=1&search="""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.has_perm("inventory.view_item"):
            return Response({"detail": "You don't have permission to view inventory."}, status=status.HTTP_403_FORBIDDEN)
        p = request.query_params
        days, duration = _int(p, "days", 7, 1, 365), _int(p, "duration", 10, 1, 365)
        rows = consumption_rows(request.user.hospital_id, categories=_ids(p.get("category")), subcategories=_ids(p.get("subcategory")),
                                days=days, duration=duration, below_only=p.get("below") in ("1", "true"), search=p.get("search", "").strip())
        cats = ItemCategory.objects.filter(hospital_id=request.user.hospital_id).order_by("name").values("id", "name", "parent")
        return Response({"days": days, "duration": duration, "rows": rows, "categories": list(cats)})


class InventoryTransferRequestView(APIView):
    """POST /inventory/consumption/request/ {department, from_store?, items: [{item, quantity}]}
    — raises a store indent (transfer request) for the selected rows."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not request.user.has_perm("inventory.add_storeindent"):
            return Response({"detail": "You don't have permission to raise a transfer request."}, status=status.HTTP_403_FORBIDDEN)
        hid = request.user.hospital_id
        department = str(request.data.get("department", "")).strip()
        if not department:
            raise ValidationError({"department": "Which department or store is asking?"})
        valid = set(Item.objects.filter(hospital_id=hid).values_list("pk", flat=True))
        lines = []
        for line in request.data.get("items") or []:
            try:
                item, qty = int(line.get("item")), int(line.get("quantity", 0))
            except (TypeError, ValueError, AttributeError):
                continue
            if item in valid and qty > 0:
                lines.append({"item": item, "quantity": qty})
        if not lines:
            raise ValidationError({"items": "Choose at least one item with a quantity."})
        store = Store.objects.filter(hospital_id=hid, pk=request.data.get("from_store") or 0).first()
        ind = StoreIndent.objects.create(hospital_id=hid, department=department[:100], from_store=store, items=lines, requested_by=request.user)
        return Response({"id": ind.pk, "indent_number": ind.indent_number, "lines": len(lines)}, status=status.HTTP_201_CREATED)


@transaction.atomic
def raise_po_from_indent(indent, user, vendor, expected_delivery=None, rates=None):
    """Draft PO for an approved indent's outstanding lines. Each line is
    priced from the vendor's rate contract, else the rate given in `rates`
    ({item id: unit cost}), else the item's last purchase or stock cost.
    Refuses a second live PO for the same indent."""
    from .contracts import _next_po_number, add_po_line, contract_line_for
    from .models import POItem

    indent = StoreIndent.objects.select_for_update().get(pk=indent.pk)
    if indent.status != StoreIndent.Status.APPROVED:
        raise ValidationError({"detail": "Only an approved indent can be turned into a PO."})
    live = indent.purchase_orders.exclude(status=PurchaseOrder.Status.CANCELLED).first()
    if live is not None:
        raise ValidationError({"detail": f"PO {live.po_number} has already been raised for indent {indent.indent_number}.", "code": "duplicate_po", "po": live.pk})
    po = PurchaseOrder.objects.create(hospital_id=indent.hospital_id, po_number=_next_po_number(indent.hospital_id), vendor=vendor,
                                      vendor_name=vendor.name, expected_delivery=expected_delivery, source_indent=indent)
    items = {i.pk: i for i in Item.objects.filter(hospital_id=indent.hospital_id, pk__in=[line.get("item") for line in indent.items or []])}
    rates = {int(k): v for k, v in (rates or {}).items() if str(k).isdigit()}
    unpriced = []
    for line in indent.items or []:
        item = items.get(int(line.get("item") or 0))
        qty = int(line.get("quantity", 0)) - int(line.get("issued_quantity", 0) or 0)
        if item is None or qty <= 0:
            continue
        cost = rates.get(item.pk)
        if cost in (None, "") and contract_line_for(po.hospital_id, vendor.pk, item.pk, timezone.localdate()) is None:
            last = POItem.objects.filter(item=item).exclude(purchase_order__status=PurchaseOrder.Status.CANCELLED).order_by("-purchase_order__ordered_at").first()
            stock = StockLevel.objects.filter(item=item, unit_cost__gt=0).order_by("-pk").first()
            cost = last.unit_cost if last else stock.unit_cost if stock else None
            if cost is None:
                unpriced.append(item.name)
                continue
        add_po_line(po, item, qty, cost)
    if unpriced:
        raise ValidationError({"rates": f"No contract or earlier price for: {', '.join(unpriced)}. Enter their unit cost.", "items": unpriced})
    if not po.po_items.exists():
        raise ValidationError({"detail": "Nothing left to order on this indent."})
    return po
