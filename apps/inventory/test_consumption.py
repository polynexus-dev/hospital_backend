"""MedNet M2.22 procurement: inventory consumption screen (multi category /
sub-category filter, safety stock, reorder quantity, transfer request) and
one live PO per requisition."""
from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.finance.models import Vendor
from apps.inventory.models import Item, ItemCategory, PurchaseOrder, StockLevel, StockTransaction, StoreIndent

C = "/api/v1/inventory/consumption/"


@pytest.fixture
def stock(hospital):
    pharma = ItemCategory.objects.create(hospital=hospital, name="Pharmaceuticals", code="PH")
    caps = ItemCategory.objects.create(hospital=hospital, name="Capsules", code="CAP", parent=pharma)
    surg = ItemCategory.objects.create(hospital=hospital, name="Surgical", code="SU")
    omez = Item.objects.create(hospital=hospital, category=caps, name="Omez 20", code="OM20", min_stock_level=10, safety_stock=20)
    gloves = Item.objects.create(hospital=hospital, category=surg, name="Gloves", code="GL", min_stock_level=50)
    StockLevel.objects.create(hospital=hospital, item=omez, batch_number="B1", quantity_on_hand=30)
    StockLevel.objects.create(hospital=hospital, item=gloves, batch_number="G1", quantity_on_hand=500, unit_cost=4)
    for days_ago, qty in ((1, -35), (3, -35), (20, -500)):  # 70 issued in the last 7 days; the old issue is outside
        StockTransaction.objects.create(hospital=hospital, item=omez, transaction_type="issue", quantity=qty, transaction_date=timezone.now() - timedelta(days=days_ago))
    StockTransaction.objects.create(hospital=hospital, item=omez, transaction_type="receipt", quantity=40, transaction_date=timezone.now() - timedelta(days=2))
    StoreIndent.objects.create(hospital=hospital, department="IP pharmacy", items=[{"item": omez.pk, "quantity": 25}])
    return {"pharma": pharma, "caps": caps, "surg": surg, "omez": omez, "gloves": gloves}


def test_consumption_figures_and_reorder_quantity(auth_client, stock):
    r = auth_client.get(f"{C}?days=7&duration=10")
    assert r.status_code == 200
    row = next(x for x in r.data["rows"] if x["code"] == "OM20")
    assert row["consumption"] == 70 and row["inward"] == 40 and row["stock_in_hand"] == 30 and row["avg_daily"] == 10
    # need 10/day x 10 days + 20 safety = 120; 120 - 30 in hand = 90; 25 already requested -> 65
    assert row["roq"] == 90 and row["already_requested"] == 25 and row["trq"] == 65 and row["below"] is True
    assert row["category"] == "Pharmaceuticals" and row["subcategory"] == "Capsules"


def test_category_and_subcategory_filters(auth_client, stock):
    by_cat = auth_client.get(f"{C}?category={stock['pharma'].pk}").data["rows"]
    assert [x["code"] for x in by_cat] == ["OM20"]  # sub-category items count under their category
    both = auth_client.get(f"{C}?category={stock['pharma'].pk},{stock['surg'].pk}").data["rows"]
    assert sorted(x["code"] for x in both) == ["GL", "OM20"]
    assert [x["code"] for x in auth_client.get(f"{C}?subcategory={stock['caps'].pk}").data["rows"]] == ["OM20"]
    assert [x["code"] for x in auth_client.get(f"{C}?below=1").data["rows"]] == ["OM20"]  # gloves well stocked


def test_transfer_request_raises_an_indent(auth_client, stock):
    r = auth_client.post(f"{C}request/", {"department": "Ward 3", "items": [{"item": stock["omez"].pk, "quantity": 65}, {"item": 999999, "quantity": 5}]}, format="json")
    assert r.status_code == 201 and r.data["lines"] == 1
    ind = StoreIndent.objects.get(indent_number=r.data["indent_number"])
    assert ind.items == [{"item": stock["omez"].pk, "quantity": 65}] and ind.department == "Ward 3"
    assert auth_client.post(f"{C}request/", {"department": "", "items": []}, format="json").status_code == 400


def test_raise_po_from_indent_once(auth_client, hospital, stock):
    vendor = Vendor.objects.create(hospital=hospital, name="M B Surgicals")
    ind = StoreIndent.objects.create(hospital=hospital, department="OT", items=[{"item": stock["gloves"].pk, "quantity": 200}], status="approved")
    url = f"/api/v1/inventory/indents/{ind.pk}/raise-po/"
    r = auth_client.post(url, {"vendor": vendor.pk}, format="json")
    assert r.status_code == 201 and r.data["lines"] == 1
    po = PurchaseOrder.objects.get(pk=r.data["id"])
    assert po.source_indent_id == ind.pk and po.po_items.get().ordered_quantity == 200
    assert po.po_items.get().unit_cost == 4  # no contract: the item's stock cost
    dup = auth_client.post(url, {"vendor": vendor.pk}, format="json")
    assert dup.status_code == 400 and dup.data["code"] == "duplicate_po"
    PurchaseOrder.objects.filter(pk=po.pk).update(status="cancelled")  # a cancelled PO frees the indent
    assert auth_client.post(url, {"vendor": vendor.pk}, format="json").status_code == 201


def test_database_refuses_two_live_pos_for_one_indent(hospital, stock):
    ind = StoreIndent.objects.create(hospital=hospital, department="OT", items=[], status="approved")
    PurchaseOrder.objects.create(hospital=hospital, po_number="A1", vendor_name="X", source_indent=ind)
    with pytest.raises(IntegrityError), transaction.atomic():
        PurchaseOrder.objects.create(hospital=hospital, po_number="A2", vendor_name="X", source_indent=ind)


def test_only_approved_indents_become_pos(auth_client, hospital, stock):
    vendor = Vendor.objects.create(hospital=hospital, name="V")
    ind = StoreIndent.objects.create(hospital=hospital, department="OT", items=[{"item": stock["gloves"].pk, "quantity": 1}])
    assert auth_client.post(f"/api/v1/inventory/indents/{ind.pk}/raise-po/", {"vendor": vendor.pk}, format="json").status_code == 400


def test_raise_po_needs_a_rate_when_nothing_is_known(auth_client, hospital, stock):
    vendor = Vendor.objects.create(hospital=hospital, name="New vendor")
    new_item = Item.objects.create(hospital=hospital, category=stock["surg"], name="Suture 2-0", code="SUT")
    ind = StoreIndent.objects.create(hospital=hospital, department="OT", items=[{"item": new_item.pk, "quantity": 10}], status="approved")
    url = f"/api/v1/inventory/indents/{ind.pk}/raise-po/"
    r = auth_client.post(url, {"vendor": vendor.pk}, format="json")
    assert r.status_code == 400 and "Suture 2-0" in str(r.data["rates"])
    assert not PurchaseOrder.objects.filter(source_indent=ind).exists()  # nothing half-made
    ok = auth_client.post(url, {"vendor": vendor.pk, "rates": {str(new_item.pk): "160"}}, format="json")
    assert ok.status_code == 201 and PurchaseOrder.objects.get(pk=ok.data["id"]).po_items.get().unit_cost == 160
