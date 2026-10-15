"""Procurement parity with MedNet M2.22: rate contracts + contract MIS,
PO sanction-date filters and delivery schedules, the cart kept between
sessions."""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.finance.models import Vendor
from apps.inventory.models import Item, ItemCategory, POItem, PurchaseOrder, RateContract, RateContractLine, Store, StoreIndent

INV = "/api/v1/inventory"


@pytest.fixture
def s(hospital):
    cat = ItemCategory.objects.create(hospital=hospital, name="Consumables", code="CON")
    gloves = Item.objects.create(hospital=hospital, category=cat, name="Surgical gloves", code="GLV")
    gauze = Item.objects.create(hospital=hospital, category=cat, name="Gauze roll", code="GZE")
    acme = Vendor.objects.create(hospital=hospital, name="Acme Surgicals")
    medi = Vendor.objects.create(hospital=hospital, name="Medi Supplies")
    today = timezone.localdate()
    rc = RateContract.objects.create(hospital=hospital, vendor=acme, contract_number="RC-01", valid_from=today - timedelta(days=30), valid_to=today + timedelta(days=20))
    RateContractLine.objects.create(hospital=hospital, contract=rc, item=gloves, rate=Decimal("12.50"), contracted_quantity=1000)
    RateContract.objects.create(hospital=hospital, vendor=acme, contract_number="RC-OLD", valid_from=today - timedelta(days=400), valid_to=today - timedelta(days=31))
    return {"gloves": gloves, "gauze": gauze, "acme": acme, "medi": medi, "rc": rc, "today": today}


def po(client, vendor, number):
    r = client.post(f"{INV}/purchase-orders/", {"po_number": number, "vendor": vendor.pk}, format="json")
    assert r.status_code == 201, r.data
    return r.data


# --- PRC-1 rate contracts ---------------------------------------------------------

def test_po_line_takes_the_contract_rate_and_needs_a_reason_to_pay_more(auth_client, s):
    p = po(auth_client, s["acme"], "PO-1")
    assert p["vendor_name"] == "Acme Surgicals"
    line = auth_client.post(f"{INV}/purchase-orders/{p['id']}/add-item/", {"item": s["gloves"].pk, "ordered_quantity": 200}, format="json").data
    assert Decimal(line["unit_cost"]) == Decimal("12.50") and line["contract_number"] == "RC-01"
    dearer = auth_client.post(f"{INV}/purchase-orders/{p['id']}/add-item/", {"item": s["gloves"].pk, "ordered_quantity": 50, "unit_cost": "14"}, format="json")
    assert dearer.status_code == 400 and "rate_override_reason" in dearer.data
    ok = auth_client.post(f"{INV}/purchase-orders/{p['id']}/add-item/", {"item": s["gloves"].pk, "ordered_quantity": 50, "unit_cost": "14", "rate_override_reason": "Urgent, contract stock out"}, format="json")
    assert ok.status_code == 201 and Decimal(ok.data["contract_rate"]) == Decimal("12.50")
    cheaper = auth_client.post(f"{INV}/purchase-orders/{p['id']}/add-item/", {"item": s["gloves"].pk, "ordered_quantity": 10, "unit_cost": "11"}, format="json")
    assert cheaper.status_code == 201 and cheaper.data["rate_override_reason"] == ""
    assert auth_client.post(f"{INV}/purchase-orders/{p['id']}/add-item/", {"item": s["gauze"].pk, "ordered_quantity": 5}, format="json").status_code == 400  # no contract, no cost
    assert auth_client.get(f"{INV}/rate-contracts/rate/", {"vendor": s["acme"].pk, "item": s["gloves"].pk}).data["rate"] == Decimal("12.50")


def test_contract_mis_variance_utilisation_off_contract_and_expiring(auth_client, s, hospital):
    on = PurchaseOrder.objects.create(hospital=hospital, po_number="PO-A", vendor=s["acme"], vendor_name="Acme Surgicals")
    line = RateContractLine.objects.get(item=s["gloves"])
    POItem.objects.create(purchase_order=on, item=s["gloves"], ordered_quantity=300, unit_cost=Decimal("12.50"), contract_line=line, contract_rate=line.rate)
    POItem.objects.create(purchase_order=on, item=s["gloves"], ordered_quantity=100, unit_cost=Decimal("14.00"), contract_line=line, contract_rate=line.rate, rate_override_reason="urgent")
    off = PurchaseOrder.objects.create(hospital=hospital, po_number="PO-B", vendor=s["medi"], vendor_name="Medi Supplies")
    POItem.objects.create(purchase_order=off, item=s["gloves"], ordered_quantity=40, unit_cost=Decimal("15.00"))

    data = auth_client.get(f"{INV}/rate-contracts/mis/", {"start": str(s["today"] - timedelta(days=5)), "end": str(s["today"])}).data
    row = next(r for r in data["lines"] if r["contract"] == "RC-01")
    assert (row["ordered_quantity"], row["utilisation_percent"], row["above_contract_lines"]) == (400, 40.0, 1)
    assert row["variance_value"] == Decimal("150.00")  # 100 x (14 - 12.50)
    assert row["average_rate"] == Decimal("12.88")
    assert data["off_contract"][0]["po_number"] == "PO-B" and data["off_contract"][0]["extra_cost"] == Decimal("100.00")
    assert [e["contract"] for e in data["expiring"]] == ["RC-01"]
    xlsx = auth_client.get(f"{INV}/rate-contracts/mis/", {"output": "xlsx"})
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    assert b"Off-contract" in auth_client.get(f"{INV}/rate-contracts/mis/", {"output": "csv", "start": str(s["today"] - timedelta(days=5))}).content


def test_contract_dates_are_checked(auth_client, s):
    r = auth_client.post(f"{INV}/rate-contracts/", {"vendor": s["medi"].pk, "contract_number": "RC-02", "valid_from": "2026-05-01", "valid_to": "2026-04-01"}, format="json")
    assert r.status_code == 400


# --- PRC-2 sanction-date filters & delivery schedule ------------------------------------

def test_sanction_date_filters_and_delivery_schedule(auth_client, s, hospital):
    a = po(auth_client, s["acme"], "PO-S1")
    b = po(auth_client, s["acme"], "PO-S2")
    auth_client.post(f"{INV}/purchase-orders/{a['id']}/approve/")
    PurchaseOrder.objects.filter(pk=a["id"]).update(approved_at=timezone.now() - timedelta(days=10))
    ids = lambda r: sorted(x["id"] for x in (r.data["results"] if isinstance(r.data, dict) else r.data))  # noqa: E731
    week_ago = str(s["today"] - timedelta(days=7))
    assert ids(auth_client.get(f"{INV}/purchase-orders/", {"sanctioned_to": week_ago})) == [a["id"]]
    assert ids(auth_client.get(f"{INV}/purchase-orders/", {"sanctioned_from": week_ago})) == []
    assert ids(auth_client.get(f"{INV}/purchase-orders/", {"sanction": "pending"})) == [b["id"]]
    assert auth_client.get(f"{INV}/purchase-orders/", {"sanctioned_from": "12/10/2026"}).status_code == 400

    line = auth_client.post(f"{INV}/purchase-orders/{a['id']}/add-item/", {"item": s["gloves"].pk, "ordered_quantity": 100}, format="json").data
    bad = auth_client.post(f"{INV}/purchase-orders/{a['id']}/schedule/", {"po_item": line["id"], "deliveries": [{"due_date": str(s["today"]), "quantity": 60}]}, format="json")
    assert bad.status_code == 400  # 60 != 100
    sched = [{"due_date": str(s["today"] - timedelta(days=3)), "quantity": 60}, {"due_date": str(s["today"] + timedelta(days=5)), "quantity": 40}]
    r = auth_client.post(f"{INV}/purchase-orders/{a['id']}/schedule/", {"po_item": line["id"], "deliveries": sched}, format="json")
    assert r.status_code == 200 and r.data["expected_delivery"] == str(s["today"] - timedelta(days=3))
    assert ids(auth_client.get(f"{INV}/purchase-orders/", {"overdue": "1"})) == [a["id"]]
    due = auth_client.get(f"{INV}/purchase-orders/deliveries/").data
    assert [(d["pending"], d["overdue"]) for d in due] == [(60, True), (40, False)]

    POItem.objects.filter(pk=line["id"]).update(received_quantity=70)  # first delivery in full + 10 of the second
    assert ids(auth_client.get(f"{INV}/purchase-orders/", {"overdue": "1"})) == []
    assert [(d["pending"], d["overdue"]) for d in auth_client.get(f"{INV}/purchase-orders/deliveries/").data] == [(30, False)]


# --- PRC-3 cart kept between sessions --------------------------------------------------

def test_cart_survives_sessions_is_per_user_and_submits(api_client, user, other_user, s, hospital):
    store = Store.objects.create(hospital=hospital, name="Main", code="MS")
    api_client.force_authenticate(user)
    body = {"header": {"department": "OT", "from_store": store.pk}, "lines": [{"item": s["gloves"].pk, "quantity": 30}, {"item": s["gauze"].pk, "quantity": 0}]}
    assert api_client.put(f"{INV}/cart/indent/", body, format="json").status_code == 200
    later = APIClient()  # a new session / device
    later.force_authenticate(user)
    cart = later.get(f"{INV}/cart/indent/").data
    assert cart["header"]["department"] == "OT" and len(cart["lines"]) == 2

    stranger = APIClient()
    stranger.force_authenticate(other_user)
    assert stranger.get(f"{INV}/cart/indent/").data["lines"] == []
    assert stranger.put(f"{INV}/cart/indent/", {"lines": [{"item": s["gloves"].pk, "quantity": 1}]}, format="json").status_code == 400  # not their item

    made = later.post(f"{INV}/cart/indent/submit/").data
    ind = StoreIndent.objects.get(pk=made["id"])
    assert ind.items == [{"item": s["gloves"].pk, "quantity": 30}] and ind.requested_by == user and ind.from_store == store
    assert later.get(f"{INV}/cart/indent/").data["lines"] == []
    assert later.post(f"{INV}/cart/indent/submit/").status_code == 400  # empty


def test_po_cart_submits_with_contract_rates(auth_client, s):
    auth_client.put(f"{INV}/cart/po/", {"header": {"vendor": s["acme"].pk}, "lines": [{"item": s["gloves"].pk, "quantity": 100}, {"item": s["gauze"].pk, "quantity": 20, "unit_cost": "35"}]}, format="json")
    made = auth_client.post(f"{INV}/cart/po/submit/").data
    po_ = PurchaseOrder.objects.get(pk=made["id"])
    assert made["lines"] == 2 and po_.vendor == s["acme"] and po_.po_number.startswith("PO")
    assert sorted(po_.po_items.values_list("unit_cost", flat=True)) == [Decimal("12.50"), Decimal("35.00")]
    auth_client.put(f"{INV}/cart/po/", {"header": {}, "lines": [{"item": s["gloves"].pk, "quantity": 1}]}, format="json")
    assert auth_client.post(f"{INV}/cart/po/submit/").status_code == 400  # no vendor; cart kept
    assert len(auth_client.get(f"{INV}/cart/po/").data["lines"]) == 1
