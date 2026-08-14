import pytest

from apps.accounts.models import Role, User, assign_role
from apps.laboratory.models import LabOrder, LabResult, LabTest, SampleCollection
from apps.patients.models import Patient


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Sunita", mobile="9988776655")


@pytest.fixture
def lab_test(hospital):
    return LabTest.objects.create(hospital=hospital, name="Complete Blood Count", code="CBC", reference_range="4.5-5.5 M/uL")


@pytest.fixture
def lab_order(hospital, patient, lab_test):
    order = LabOrder.objects.create(hospital=hospital, patient=patient)
    order.ordered_tests.add(lab_test)
    return order


@pytest.fixture
def lab_manager_user(hospital, department):
    role = Role.objects.create(hospital=hospital, department=department, name="Pathologist", template=Role.Template.LAB_MANAGER)
    user = User.objects.create_user(email="pathologist@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(user, role)
    return user


@pytest.fixture
def lab_manager_client(api_client, lab_manager_user):
    api_client.force_authenticate(user=lab_manager_user)
    yield api_client
    api_client.force_authenticate(user=None)


@pytest.fixture
def lab_technician_user(hospital, department):
    role = Role.objects.create(hospital=hospital, department=department, name="Lab Tech", template=Role.Template.LAB_TECHNICIAN)
    user = User.objects.create_user(email="labtech@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(user, role)
    return user


@pytest.fixture
def lab_technician_client(api_client, lab_technician_user):
    api_client.force_authenticate(user=lab_technician_user)
    yield api_client
    api_client.force_authenticate(user=None)


# --- LabOrder lifecycle --------------------------------------------------

@pytest.mark.django_db
def test_lab_order_api_create_stamps_ordered_by(auth_client, user, patient, lab_test):
    response = auth_client.post("/api/v1/laboratory/orders/", {
        "patient": patient.id, "ordered_tests": [lab_test.id],
    }, format="json")
    assert response.status_code == 201
    order = LabOrder.objects.get(pk=response.data["id"])
    assert order.ordered_by_id == user.id
    assert order.status == LabOrder.Status.ORDERED


@pytest.mark.django_db
def test_sample_collection_advances_order_status(auth_client, user, lab_order):
    response = auth_client.post("/api/v1/laboratory/samples/", {
        "lab_order": lab_order.id, "sample_type": "Blood", "barcode": "BC-0001",
    }, format="json")
    assert response.status_code == 201
    collection = SampleCollection.objects.get(pk=response.data["id"])
    assert collection.collected_by_id == user.id

    lab_order.refresh_from_db()
    assert lab_order.status == LabOrder.Status.SAMPLE_COLLECTED


# --- LabResult: creation, order status sync, verify-locking, RBAC -------

@pytest.mark.django_db
def test_lab_result_api_create_stamps_entered_by_and_syncs_order_to_resulted(auth_client, user, lab_order, lab_test):
    response = auth_client.post("/api/v1/laboratory/results/", {
        "lab_order": lab_order.id, "lab_test": lab_test.id, "value": "5.1", "unit": "M/uL", "flag": "normal",
    }, format="json")
    assert response.status_code == 201
    result = LabResult.objects.get(pk=response.data["id"])
    assert result.entered_by_id == user.id

    lab_order.refresh_from_db()
    assert lab_order.status == LabOrder.Status.RESULTED


@pytest.mark.django_db
def test_verify_locks_result_against_further_edits_and_marks_order_verified(lab_manager_client, hospital, lab_order, lab_test):
    create = lab_manager_client.post("/api/v1/laboratory/results/", {
        "lab_order": lab_order.id, "lab_test": lab_test.id, "value": "5.1", "flag": "normal",
    }, format="json")
    result_id = create.data["id"]

    verify = lab_manager_client.post(f"/api/v1/laboratory/results/{result_id}/verify/")
    assert verify.status_code == 200
    assert verify.data["finalized_at"] is not None

    result = LabResult.objects.get(pk=result_id)
    result.value = "9.9"
    with pytest.raises(ValueError):
        result.save()
    result.refresh_from_db()
    assert result.value == "5.1"

    lab_order.refresh_from_db()
    assert lab_order.status == LabOrder.Status.VERIFIED


@pytest.mark.django_db
def test_lab_technician_can_enter_results_but_cannot_verify(lab_technician_client, lab_order, lab_test):
    create = lab_technician_client.post("/api/v1/laboratory/results/", {
        "lab_order": lab_order.id, "lab_test": lab_test.id, "value": "5.1", "flag": "normal",
    }, format="json")
    assert create.status_code == 201

    verify = lab_technician_client.post(f"/api/v1/laboratory/results/{create.data['id']}/verify/")
    assert verify.status_code == 403


# --- Critical-result signal -> apps.automation urgent task ---------------

@pytest.mark.django_db
def test_critical_result_fires_result_critical_signal(auth_client, lab_order, lab_test):
    """result_critical fires from LabResultViewSet.perform_create, not from
    LabResult.objects.create() itself — go through the API, matching how
    it's actually wired (see apps.laboratory.views._maybe_alert_critical)."""
    from apps.laboratory.signals import result_critical

    received = []

    def _handler(sender, result, **kwargs):
        received.append(result)

    result_critical.connect(_handler, weak=False)
    try:
        response = auth_client.post("/api/v1/laboratory/results/", {
            "lab_order": lab_order.id, "lab_test": lab_test.id, "value": "20", "flag": "critical",
        }, format="json")
    finally:
        result_critical.disconnect(_handler)

    assert response.status_code == 201
    assert len(received) == 1
    assert received[0].flag == LabResult.Flag.CRITICAL


@pytest.mark.django_db
def test_critical_lab_result_creates_urgent_automation_task(auth_client, lab_order, lab_test):
    from apps.automation.models import Task

    response = auth_client.post("/api/v1/laboratory/results/", {
        "lab_order": lab_order.id, "lab_test": lab_test.id, "value": "20", "flag": "critical",
    }, format="json")
    assert response.status_code == 201

    result = LabResult.objects.get(pk=response.data["id"])
    task = Task.objects.get(content_type__model="labresult", object_id=result.pk)
    assert task.priority == Task.Priority.URGENT


@pytest.mark.django_db
def test_normal_result_does_not_create_an_automation_task(auth_client, lab_order, lab_test):
    from apps.automation.models import Task

    before = Task.objects.count()
    auth_client.post("/api/v1/laboratory/results/", {
        "lab_order": lab_order.id, "lab_test": lab_test.id, "value": "5.1", "flag": "normal",
    }, format="json")
    assert Task.objects.count() == before


# --- RBAC: whole-ViewSet clinical-detail gate, receptionist blocked -----

@pytest.mark.django_db
def test_receptionist_is_blocked_from_lab_orders_and_results(receptionist_client_lab, lab_order):
    assert receptionist_client_lab.get("/api/v1/laboratory/orders/").status_code == 403
    assert receptionist_client_lab.get("/api/v1/laboratory/results/").status_code == 403


@pytest.fixture
def receptionist_client_lab(api_client, hospital, department):
    role = Role.objects.create(hospital=hospital, department=department, name="Receptionist", template=Role.Template.RECEPTIONIST)
    receptionist = User.objects.create_user(email="reception-lab@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(receptionist, role)
    api_client.force_authenticate(user=receptionist)
    yield api_client
    api_client.force_authenticate(user=None)


@pytest.mark.django_db
def test_lab_test_catalogue_is_visible_to_receptionist(receptionist_client_lab, lab_test):
    """LabTest is catalogue data, not clinical content — no
    access_clinical_detail gate, same reasoning as HealthPackage."""
    response = receptionist_client_lab.get("/api/v1/laboratory/tests/")
    assert response.status_code == 200


# --- Tenant isolation ------------------------------------------------------

@pytest.mark.django_db
def test_lab_order_isolation(auth_client, other_hospital, other_department):
    other_patient = Patient.objects.create(hospital=other_hospital, first_name="Theirs", mobile="9000000099")
    theirs = LabOrder.objects.create(hospital=other_hospital, patient=other_patient)

    assert auth_client.get(f"/api/v1/laboratory/orders/{theirs.id}/").status_code == 404
