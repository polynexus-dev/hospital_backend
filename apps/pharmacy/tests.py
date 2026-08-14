import datetime

import pytest

from apps.accounts.models import Role, User, assign_role
from apps.patients.models import Patient, Prescription
from apps.pharmacy.models import DispenseRecord, Medicine, MedicineBatch, StockAdjustment
from apps.pharmacy.services import InsufficientStock, adjust_stock, dispense_medicine


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Sunita", mobile="9988776655")


@pytest.fixture
def medicine(hospital):
    return Medicine.objects.create(hospital=hospital, name="Paracetamol 500mg", reorder_level=10)


@pytest.fixture
def batch(hospital, medicine):
    return MedicineBatch.objects.create(
        hospital=hospital, medicine=medicine, batch_number="B-001",
        expiry_date=datetime.date.today() + datetime.timedelta(days=365), quantity_available=50,
    )


@pytest.fixture
def prescription(hospital, patient):
    return Prescription.objects.create(hospital=hospital, patient=patient, diagnosis="Fever")


@pytest.fixture
def pharmacist_user(hospital, department):
    role = Role.objects.create(hospital=hospital, department=department, name="Pharmacist", template=Role.Template.PHARMACIST)
    user = User.objects.create_user(email="pharmacist@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(user, role)
    return user


@pytest.fixture
def pharmacist_client(api_client, pharmacist_user):
    api_client.force_authenticate(user=pharmacist_user)
    yield api_client
    api_client.force_authenticate(user=None)


@pytest.fixture
def receptionist_client_pharmacy(api_client, hospital, department):
    role = Role.objects.create(hospital=hospital, department=department, name="Receptionist", template=Role.Template.RECEPTIONIST)
    receptionist = User.objects.create_user(email="reception-pharma@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(receptionist, role)
    api_client.force_authenticate(user=receptionist)
    yield api_client
    api_client.force_authenticate(user=None)


# --- dispense_medicine service: race-safe stock decrement -----------------

@pytest.mark.django_db
def test_dispense_medicine_decrements_batch_stock(hospital, batch, pharmacist_user):
    record = dispense_medicine(hospital=hospital, batch=batch, quantity=5, dispensed_by=pharmacist_user)

    batch.refresh_from_db()
    assert batch.quantity_available == 45
    assert record.quantity == 5
    assert record.dispensed_by_id == pharmacist_user.id


@pytest.mark.django_db
def test_dispense_medicine_raises_when_stock_insufficient(hospital, batch, pharmacist_user):
    with pytest.raises(InsufficientStock):
        dispense_medicine(hospital=hospital, batch=batch, quantity=999, dispensed_by=pharmacist_user)

    batch.refresh_from_db()
    assert batch.quantity_available == 50  # unchanged — failed dispense doesn't partially decrement


# --- adjust_stock service --------------------------------------------------

@pytest.mark.django_db
def test_adjust_stock_positive_delta_increases_quantity(hospital, batch, pharmacist_user):
    adjustment = adjust_stock(hospital=hospital, batch=batch, adjustment_type=StockAdjustment.AdjustmentType.CORRECTION, quantity_delta=10, reason="Recount", adjusted_by=pharmacist_user)

    batch.refresh_from_db()
    assert batch.quantity_available == 60
    assert adjustment.quantity_delta == 10


@pytest.mark.django_db
def test_adjust_stock_raises_when_it_would_go_below_zero(hospital, batch, pharmacist_user):
    with pytest.raises(InsufficientStock):
        adjust_stock(hospital=hospital, batch=batch, adjustment_type=StockAdjustment.AdjustmentType.DAMAGE, quantity_delta=-999, reason="Water damage", adjusted_by=pharmacist_user)

    batch.refresh_from_db()
    assert batch.quantity_available == 50


# --- DispenseRecordViewSet.create: API-level, 409 on insufficient stock ---

@pytest.mark.django_db
def test_dispense_record_api_create_decrements_stock_and_stamps_dispensed_by(pharmacist_client, pharmacist_user, batch, prescription):
    response = pharmacist_client.post("/api/v1/pharmacy/dispense-records/", {
        "batch": batch.id, "quantity": 5, "prescription": prescription.id,
    }, format="json")
    assert response.status_code == 201

    record = DispenseRecord.objects.get(pk=response.data["id"])
    assert record.dispensed_by_id == pharmacist_user.id
    assert record.quantity == 5

    batch.refresh_from_db()
    assert batch.quantity_available == 45


@pytest.mark.django_db
def test_dispense_record_api_returns_409_on_insufficient_stock(pharmacist_client, batch):
    response = pharmacist_client.post("/api/v1/pharmacy/dispense-records/", {
        "batch": batch.id, "quantity": 999,
    }, format="json")
    assert response.status_code == 409

    batch.refresh_from_db()
    assert batch.quantity_available == 50


@pytest.mark.django_db
def test_dispense_record_api_allows_no_prescription_for_otc_dispensing(pharmacist_client, batch):
    response = pharmacist_client.post("/api/v1/pharmacy/dispense-records/", {"batch": batch.id, "quantity": 2}, format="json")
    assert response.status_code == 201
    assert DispenseRecord.objects.get(pk=response.data["id"]).prescription_id is None


# --- StockAdjustmentViewSet.create -----------------------------------------

@pytest.mark.django_db
def test_stock_adjustment_api_create(pharmacist_client, pharmacist_user, batch):
    response = pharmacist_client.post("/api/v1/pharmacy/stock-adjustments/", {
        "batch": batch.id, "adjustment_type": "expiry", "quantity_delta": -10, "reason": "Past expiry",
    }, format="json")
    assert response.status_code == 201
    assert StockAdjustment.objects.get(pk=response.data["id"]).adjusted_by_id == pharmacist_user.id

    batch.refresh_from_db()
    assert batch.quantity_available == 40


# --- Medicine catalogue: total_available, RBAC -----------------------------

@pytest.mark.django_db
def test_medicine_total_available_sums_across_batches(auth_client, hospital, medicine, batch):
    MedicineBatch.objects.create(hospital=hospital, medicine=medicine, batch_number="B-002", expiry_date=datetime.date.today() + datetime.timedelta(days=200), quantity_available=15)

    response = auth_client.get(f"/api/v1/pharmacy/medicines/{medicine.id}/")
    assert response.status_code == 200
    assert response.data["total_available"] == 65


@pytest.mark.django_db
def test_medicine_catalogue_is_visible_to_receptionist(receptionist_client_pharmacy, medicine):
    """Medicine/MedicineBatch/Supplier are catalogue data, not clinical
    content — no access_clinical_detail gate, same reasoning as
    apps.laboratory.LabTestViewSet."""
    response = receptionist_client_pharmacy.get("/api/v1/pharmacy/medicines/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_receptionist_is_blocked_from_dispensing(receptionist_client_pharmacy, batch):
    response = receptionist_client_pharmacy.post("/api/v1/pharmacy/dispense-records/", {"batch": batch.id, "quantity": 1}, format="json")
    assert response.status_code == 403


# --- Tenant isolation -------------------------------------------------------

@pytest.mark.django_db
def test_medicine_batch_isolation(auth_client, other_hospital):
    other_medicine = Medicine.objects.create(hospital=other_hospital, name="Theirs")
    theirs = MedicineBatch.objects.create(hospital=other_hospital, medicine=other_medicine, batch_number="X-1", expiry_date=datetime.date.today() + datetime.timedelta(days=100), quantity_available=5)

    assert auth_client.get(f"/api/v1/pharmacy/batches/{theirs.id}/").status_code == 404
