from decimal import Decimal

import pytest

from apps.facilities.models import Ward
from apps.support_services.models import LaundryBatch, LinenStock, LinenType


@pytest.fixture
def linen(hospital):
    ward = Ward.objects.create(hospital=hospital, name="Ward A")
    sheet = LinenType.objects.create(hospital=hospital, name="Bedsheet", unit_cost=350)
    gown = LinenType.objects.create(hospital=hospital, name="Patient gown", unit_cost=250)
    LinenStock.objects.create(hospital=hospital, ward=None, linen_type=sheet, clean_qty=100)
    LinenStock.objects.create(hospital=hospital, ward=ward, linen_type=sheet, par_level=40, clean_qty=10)
    return {"ward": ward, "sheet": sheet, "gown": gown}


def new_batch(client, linen, kind="soiled"):
    res = client.post("/api/v1/support-services/laundry-batches/", {
        "ward": linen["ward"].pk, "kind": kind, "vendor": "CleanCo", "rate_per_kg": "40",
        "lines": [{"linen_type": linen["sheet"].pk, "sent_qty": 20}, {"linen_type": linen["gown"].pk, "sent_qty": 10}],
    }, format="json")
    assert res.status_code == 201, res.data
    return res.data["id"]


@pytest.mark.django_db
def test_infected_linen_needs_a_validated_wash(auth_client, linen):
    bid = new_batch(auth_client, linen, kind="infected")
    base = f"/api/v1/support-services/laundry-batches/{bid}/"
    assert auth_client.post(base + "process/", {"wash_temp_c": 60, "wash_minutes": 10}, format="json").status_code == 400
    assert auth_client.post(base + "process/", {"wash_temp_c": 71, "wash_minutes": 2}, format="json").status_code == 400
    ok = auth_client.post(base + "process/", {"wash_temp_c": 75, "wash_minutes": 10}, format="json")
    assert ok.status_code == 200 and ok.data["status"] == "processed"
    assert auth_client.post(base + "process/", {"wash_temp_c": 75, "wash_minutes": 10}, format="json").status_code == 400

    chem = new_batch(auth_client, linen, kind="infected")
    assert auth_client.post(f"/api/v1/support-services/laundry-batches/{chem}/process/", {"disinfectant": "1% hypochlorite, 30 min"}, format="json").status_code == 200


@pytest.mark.django_db
def test_return_restocks_the_ward_and_tracks_losses(auth_client, linen):
    bid = new_batch(auth_client, linen)
    base = f"/api/v1/support-services/laundry-batches/{bid}/"
    assert auth_client.post(base + "return/", {"lines": []}, format="json").status_code == 400  # not washed yet
    auth_client.post(base + "process/", {"wash_temp_c": 60, "wash_minutes": 20}, format="json")
    too_many = auth_client.post(base + "return/", {"lines": [{"linen_type": linen["sheet"].pk, "returned_qty": 19, "rejected_qty": 2}]}, format="json")
    assert too_many.status_code == 400
    res = auth_client.post(base + "return/", {"weight_kg": "12.5", "lines": [
        {"linen_type": linen["sheet"].pk, "returned_qty": 17, "rejected_qty": 1},
        {"linen_type": linen["gown"].pk, "returned_qty": 10},
    ]}, format="json")
    assert res.status_code == 200 and res.data["status"] == "returned" and Decimal(res.data["cost"]) == Decimal("500.00")
    assert LinenStock.objects.get(ward=linen["ward"], linen_type=linen["sheet"]).clean_qty == 27

    dash = auth_client.get("/api/v1/support-services/laundry-batches/dashboard/").data
    assert dash["losses_30d"]["Bedsheet"] == {"sent": 20, "lost": 2, "condemned": 1, "value_lost": Decimal("1050")}
    assert dash["shortfalls"] == [{"location": "Ward A", "linen": "Bedsheet", "par": 40, "clean": 27, "short_by": 13}]


@pytest.mark.django_db
def test_issue_moves_clean_stock_from_central_store(auth_client, linen):
    res = auth_client.post("/api/v1/support-services/linen-stock/issue/", {"linen_type": linen["sheet"].pk, "ward": linen["ward"].pk, "quantity": 30}, format="json")
    assert res.status_code == 200 and res.data["ward_clean_qty"] == 40
    assert LinenStock.objects.get(ward__isnull=True, linen_type=linen["sheet"]).clean_qty == 70
    assert auth_client.post("/api/v1/support-services/linen-stock/issue/", {"linen_type": linen["sheet"].pk, "ward": linen["ward"].pk, "quantity": 500}, format="json").status_code == 400
    assert LaundryBatch.objects.count() == 0
