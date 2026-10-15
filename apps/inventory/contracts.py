"""Rate contracts (PRC-1), PO delivery schedules (PRC-2) and the indent /
PO cart (PRC-3).

Pricing a PO line: the PO's vendor's contract valid on the order date sets
the rate. Leaving the cost blank takes it; a lower cost is accepted; a
higher one needs a reason. Both are kept, so the contract-vs-actual report
can show the variance.
"""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import Item, POItem, PurchaseOrder, StoreIndent
from .models_contracts import PODelivery, ProcurementCart, RateContract, RateContractLine

ZERO = Decimal("0")


def _day(dt):
    return timezone.localdate(dt) if dt else timezone.localdate()


def contract_line_for(hospital_id, vendor_id, item_id, day):
    """The lowest-rate line for the item in the vendor's contracts valid on `day`."""
    if not vendor_id:
        return None
    return (
        RateContractLine.objects.filter(
            hospital_id=hospital_id, item_id=item_id, contract__vendor_id=vendor_id, contract__is_active=True,
            contract__valid_from__lte=day, contract__valid_to__gte=day,
        )
        .select_related("contract")
        .order_by("rate")
        .first()
    )


def best_contract_rate(hospital_id, item_id, day):
    """Lowest valid contract rate for the item from any vendor (for off-contract checks)."""
    return (
        RateContractLine.objects.filter(hospital_id=hospital_id, item_id=item_id, contract__is_active=True,
                                        contract__valid_from__lte=day, contract__valid_to__gte=day)
        .select_related("contract__vendor").order_by("rate").first()
    )


def price_po_line(po, item, unit_cost=None, reason=""):
    """(unit_cost, contract_line, contract_rate) for a new PO line."""
    line = contract_line_for(po.hospital_id, po.vendor_id, item.pk, _day(po.ordered_at))
    cost = None if unit_cost in (None, "") else Decimal(str(unit_cost))
    if cost is not None and cost < 0:
        raise ValidationError({"unit_cost": "Can't be negative."})
    if line is None:
        if cost is None:
            raise ValidationError({"unit_cost": f"No rate contract with this vendor for {item.name}; enter the unit cost."})
        return cost, None, None
    if cost is None:
        return line.rate, line, line.rate
    if cost > line.rate and not (reason or "").strip():
        raise ValidationError({"rate_override_reason": f"Contract {line.contract.contract_number} rate is ₹{line.rate}; give a reason to order at ₹{cost}."})
    return cost, line, line.rate


def add_po_line(po, item, quantity, unit_cost=None, reason=""):
    if po.status != PurchaseOrder.Status.DRAFT:
        raise ValidationError({"detail": "Lines can only be added to a draft PO."})
    if int(quantity) <= 0:
        raise ValidationError({"ordered_quantity": "Must be at least 1."})
    cost, line, rate = price_po_line(po, item, unit_cost, reason)
    return POItem.objects.create(purchase_order=po, item=item, ordered_quantity=int(quantity), unit_cost=cost,
                                 contract_line=line, contract_rate=rate, rate_override_reason=(reason or "").strip()[:255] if cost != rate else "")


# --- contract vs actual -------------------------------------------------------------

def contract_mis(hospital_id, start, end, vendor_id=None):
    """Per contract line: what was ordered on it in the period, at what
    rate, the variance against the contract rate and how much of the
    contracted quantity is used; plus off-contract buying — PO lines for
    items that had a valid contract but were bought without it, and what
    that cost over the best contract rate."""
    pos = POItem.objects.filter(purchase_order__hospital_id=hospital_id, purchase_order__ordered_at__date__range=(start, end)).exclude(
        purchase_order__status=PurchaseOrder.Status.CANCELLED).select_related("purchase_order__vendor", "item", "contract_line__contract__vendor")
    if vendor_id:
        pos = pos.filter(purchase_order__vendor_id=vendor_id)
    by_line = defaultdict(list)
    off = []
    for p in pos:
        if p.contract_line_id:
            by_line[p.contract_line_id].append(p)
            continue
        best = best_contract_rate(hospital_id, p.item_id, _day(p.purchase_order.ordered_at))
        if best is not None and p.unit_cost > best.rate:
            off.append({
                "po_number": p.purchase_order.po_number, "ordered_on": _day(p.purchase_order.ordered_at), "vendor": p.purchase_order.vendor_name,
                "item": p.item.name, "quantity": p.ordered_quantity, "rate": p.unit_cost, "best_contract_rate": best.rate,
                "best_contract": best.contract.contract_number, "best_contract_vendor": best.contract.vendor.name,
                "extra_cost": (p.unit_cost - best.rate) * p.ordered_quantity,
            })
    lines = RateContractLine.objects.filter(hospital_id=hospital_id, contract__valid_from__lte=end, contract__valid_to__gte=start).select_related("contract__vendor", "item")
    if vendor_id:
        lines = lines.filter(contract__vendor_id=vendor_id)
    rows = []
    for ln in lines:
        items = by_line.get(ln.pk, [])
        qty = sum(p.ordered_quantity for p in items)
        value = sum((p.unit_cost * p.ordered_quantity for p in items), ZERO)
        rows.append({
            "contract": ln.contract.contract_number, "vendor": ln.contract.vendor.name, "valid_from": ln.contract.valid_from, "valid_to": ln.contract.valid_to,
            "item": ln.item.name, "contract_rate": ln.rate, "contracted_quantity": ln.contracted_quantity,
            "ordered_quantity": qty, "received_quantity": sum(p.received_quantity for p in items), "po_lines": len(items),
            "average_rate": (value / qty).quantize(Decimal("0.01")) if qty else None,
            "variance_value": value - ln.rate * qty,
            "utilisation_percent": round(100 * qty / ln.contracted_quantity, 1) if ln.contracted_quantity else None,
            "above_contract_lines": sum(1 for p in items if p.unit_cost > ln.rate),
        })
    today = timezone.localdate()
    expiring = [
        {"contract": c.contract_number, "vendor": c.vendor.name, "valid_to": c.valid_to, "days_left": (c.valid_to - today).days}
        for c in RateContract.objects.filter(hospital_id=hospital_id, is_active=True, valid_to__range=(today, today + timezone.timedelta(days=30))).select_related("vendor")
    ]
    return {
        "lines": rows, "off_contract": off, "expiring": expiring,
        "totals": {
            "variance_value": sum((r["variance_value"] for r in rows), ZERO),
            "off_contract_extra_cost": sum((o["extra_cost"] for o in off), ZERO),
        },
    }


# --- delivery schedule ----------------------------------------------------------------

def schedule_rows(po_items, today=None):
    """Each scheduled delivery with what's still pending on it: a PO line's
    received quantity fills its deliveries in date order."""
    today = today or timezone.localdate()
    out = []
    for poi in po_items:
        received = poi.received_quantity
        for d in poi.deliveries.all():
            filled = min(received, d.quantity)
            received -= filled
            pending = d.quantity - filled
            out.append({
                "id": d.pk, "po": poi.purchase_order_id, "po_number": poi.purchase_order.po_number, "vendor": poi.purchase_order.vendor_name,
                "po_item": poi.pk, "item": poi.item.name, "due_date": d.due_date, "quantity": d.quantity, "pending": pending,
                "overdue": pending > 0 and d.due_date < today, "days_late": (today - d.due_date).days if pending > 0 and d.due_date < today else 0,
            })
    return out


@transaction.atomic
def set_schedule(po_item, rows):
    """Replaces a PO line's delivery schedule. rows: [{due_date, quantity}];
    the quantities must add up to the ordered quantity."""
    from django.utils.dateparse import parse_date

    parsed = []
    for r in rows or []:
        day = parse_date(str(r.get("due_date") or ""))
        qty = int(r.get("quantity") or 0)
        if day is None or qty <= 0:
            raise ValidationError({"schedule": "Each delivery needs a due date and a quantity of at least 1."})
        parsed.append((day, qty))
    if parsed and sum(q for _d, q in parsed) != po_item.ordered_quantity:
        raise ValidationError({"schedule": f"Deliveries add up to {sum(q for _d, q in parsed)}; the line orders {po_item.ordered_quantity}."})
    PODelivery.objects.filter(po_item=po_item).delete()
    for day, qty in sorted(parsed):
        PODelivery.objects.create(hospital_id=po_item.purchase_order.hospital_id, po_item=po_item, due_date=day, quantity=qty)
    po = po_item.purchase_order
    first = PODelivery.objects.filter(po_item__purchase_order=po).order_by("due_date").values_list("due_date", flat=True).first()
    if first and first != po.expected_delivery:
        po.expected_delivery = first
        po.save(update_fields=["expected_delivery"])


# --- cart ---------------------------------------------------------------------------------

def get_cart(user, kind):
    cart, _ = ProcurementCart.objects.get_or_create(user=user, kind=kind, defaults={"hospital_id": user.hospital_id})
    return cart


def _clean_lines(hospital_id, lines):
    if not isinstance(lines, list):
        raise ValidationError({"lines": "A list of lines."})
    ids = {int(l.get("item")) for l in lines if isinstance(l, dict) and str(l.get("item", "")).isdigit()}
    known = set(Item.objects.filter(hospital_id=hospital_id, pk__in=ids).values_list("pk", flat=True))
    out = []
    for l in lines:
        if not isinstance(l, dict) or not str(l.get("item", "")).isdigit() or int(l["item"]) not in known:
            raise ValidationError({"lines": "Every line needs an item from this hospital's catalogue."})
        try:
            qty = int(l.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        line = {"item": int(l["item"]), "quantity": qty}
        for k in ("unit_cost", "rate_override_reason", "note"):
            if l.get(k) not in (None, ""):
                line[k] = str(l[k])[:255]
        out.append(line)
    return out


def save_cart(user, kind, header, lines):
    cart = get_cart(user, kind)
    cart.header = header if isinstance(header, dict) else {}
    cart.lines = _clean_lines(user.hospital_id, lines)
    cart.saved_at = timezone.now()
    cart.save()
    return cart


@transaction.atomic
def submit_cart(user, kind):
    """Turns the cart into an indent or a draft PO (lines priced from rate
    contracts) and empties it. Returns the new record."""
    cart = ProcurementCart.objects.select_for_update().get(pk=get_cart(user, kind).pk)
    lines = [l for l in cart.lines if l.get("quantity", 0) > 0]
    if not lines:
        raise ValidationError({"lines": "The cart is empty."})
    h = cart.header or {}
    if kind == ProcurementCart.Kind.INDENT:
        if not str(h.get("department", "")).strip():
            raise ValidationError({"department": "Which department is the indent for?"})
        from .models import Store

        store = Store.objects.filter(hospital_id=user.hospital_id, pk=h.get("from_store") or 0).first()
        record = StoreIndent.objects.create(hospital_id=user.hospital_id, department=str(h["department"])[:100], from_store=store,
                                            items=[{"item": l["item"], "quantity": l["quantity"]} for l in lines], requested_by=user)
    else:
        from apps.finance.models import Vendor

        vendor = Vendor.objects.filter(hospital_id=user.hospital_id, pk=h.get("vendor") or 0).first()
        if vendor is None:
            raise ValidationError({"vendor": "Choose the vendor."})
        number = str(h.get("po_number") or "").strip() or _next_po_number(user.hospital_id)
        if PurchaseOrder.objects.filter(hospital_id=user.hospital_id, po_number=number).exists():
            raise ValidationError({"po_number": f"PO {number} already exists."})
        record = PurchaseOrder.objects.create(hospital_id=user.hospital_id, po_number=number, vendor=vendor, vendor_name=vendor.name,
                                              expected_delivery=h.get("expected_delivery") or None)
        items = {i.pk: i for i in Item.objects.filter(hospital_id=user.hospital_id, pk__in=[l["item"] for l in lines])}
        for l in lines:
            add_po_line(record, items[l["item"]], l["quantity"], l.get("unit_cost"), l.get("rate_override_reason", ""))
    cart.header, cart.lines, cart.saved_at = {}, [], timezone.now()
    cart.save()
    return record


def _next_po_number(hospital_id):
    n = PurchaseOrder.objects.filter(hospital_id=hospital_id).count() + 1
    while PurchaseOrder.objects.filter(hospital_id=hospital_id, po_number=f"PO{timezone.localdate():%y}{n:05d}").exists():
        n += 1
    return f"PO{timezone.localdate():%y}{n:05d}"


def po_filters(qs, params):
    """PRC-2 list filters: sanction (approval) date, order date, delivery date, vendor, sanction state, overdue."""
    from django.utils.dateparse import parse_date

    ranges = {"sanctioned": "approved_at__date", "ordered": "ordered_at__date", "delivery": "expected_delivery"}
    for key, field in ranges.items():
        for bound, op in (("from", "gte"), ("to", "lte")):
            raw = params.get(f"{key}_{bound}")
            if raw:
                day = parse_date(raw)
                if day is None:
                    raise ValidationError({f"{key}_{bound}": "Use YYYY-MM-DD."})
                qs = qs.filter(**{f"{field}__{op}": day})
    if params.get("vendor"):
        qs = qs.filter(vendor_id=params["vendor"])
    if params.get("sanction") == "pending":
        qs = qs.filter(approved_at__isnull=True)
    elif params.get("sanction") == "done":
        qs = qs.filter(approved_at__isnull=False)
    if params.get("overdue") in ("1", "true"):
        qs = qs.filter(pk__in=overdue_po_ids(qs))
    return qs


def overdue_po_ids(qs):
    """Open POs with a scheduled delivery past due and not yet received (or,
    with no schedule, past their expected delivery date)."""
    today = timezone.localdate()
    open_pos = qs.exclude(status__in=[PurchaseOrder.Status.RECEIVED, PurchaseOrder.Status.CANCELLED])
    items = POItem.objects.filter(purchase_order__in=open_pos).select_related("purchase_order", "item").prefetch_related("deliveries")
    late = {r["po"] for r in schedule_rows(items, today) if r["overdue"]}
    scheduled = set(PODelivery.objects.filter(po_item__purchase_order__in=open_pos).values_list("po_item__purchase_order", flat=True))
    late |= set(open_pos.filter(expected_delivery__lt=today).exclude(pk__in=scheduled).values_list("pk", flat=True))
    return late
