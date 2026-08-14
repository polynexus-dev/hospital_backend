import pytest

from apps.accounts.models import Role, User, assign_role
from apps.patients.models import Patient
from apps.radiology.models import RadiologyOrder, RadiologyProcedure, RadiologyReport


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Sunita", mobile="9988776655")


@pytest.fixture
def procedure(hospital):
    return RadiologyProcedure.objects.create(hospital=hospital, name="Chest X-Ray", modality=RadiologyProcedure.Modality.XRAY)


@pytest.fixture
def radiology_order(hospital, patient, procedure):
    return RadiologyOrder.objects.create(hospital=hospital, patient=patient, procedure=procedure)


@pytest.fixture
def radiologist_user(hospital, department):
    role = Role.objects.create(hospital=hospital, department=department, name="Radiologist", template=Role.Template.RADIOLOGIST)
    user = User.objects.create_user(email="radiologist@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(user, role)
    return user


@pytest.fixture
def radiologist_client(api_client, radiologist_user):
    api_client.force_authenticate(user=radiologist_user)
    yield api_client
    api_client.force_authenticate(user=None)


@pytest.fixture
def radiology_technician_user(hospital, department):
    role = Role.objects.create(hospital=hospital, department=department, name="Radiology Tech", template=Role.Template.RADIOLOGY_TECHNICIAN)
    user = User.objects.create_user(email="radtech@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(user, role)
    return user


@pytest.fixture
def radiology_technician_client(api_client, radiology_technician_user):
    api_client.force_authenticate(user=radiology_technician_user)
    yield api_client
    api_client.force_authenticate(user=None)


@pytest.fixture
def receptionist_client_radiology(api_client, hospital, department):
    role = Role.objects.create(hospital=hospital, department=department, name="Receptionist", template=Role.Template.RECEPTIONIST)
    receptionist = User.objects.create_user(email="reception-rad@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(receptionist, role)
    api_client.force_authenticate(user=receptionist)
    yield api_client
    api_client.force_authenticate(user=None)


# --- RadiologyOrder lifecycle ---------------------------------------------

@pytest.mark.django_db
def test_radiology_order_api_create_stamps_ordered_by(auth_client, user, patient, procedure):
    response = auth_client.post("/api/v1/radiology/orders/", {
        "patient": patient.id, "procedure": procedure.id,
    }, format="json")
    assert response.status_code == 201
    order = RadiologyOrder.objects.get(pk=response.data["id"])
    assert order.ordered_by_id == user.id
    assert order.status == RadiologyOrder.Status.ORDERED


# --- RadiologyReport: creation, order status sync, verify-locking, RBAC --

@pytest.mark.django_db
def test_radiology_report_api_create_stamps_reported_by_and_syncs_order_to_reported(auth_client, user, radiology_order):
    response = auth_client.post("/api/v1/radiology/reports/", {
        "radiology_order": radiology_order.id, "findings": "No acute abnormality", "impression": "Normal study",
    }, format="json")
    assert response.status_code == 201
    report = RadiologyReport.objects.get(pk=response.data["id"])
    assert report.reported_by_id == user.id

    radiology_order.refresh_from_db()
    assert radiology_order.status == RadiologyOrder.Status.REPORTED


@pytest.mark.django_db
def test_verify_locks_report_against_further_edits(radiologist_client, radiology_order):
    create = radiologist_client.post("/api/v1/radiology/reports/", {
        "radiology_order": radiology_order.id, "findings": "Clear lung fields", "impression": "Normal",
    }, format="json")
    report_id = create.data["id"]

    verify = radiologist_client.post(f"/api/v1/radiology/reports/{report_id}/verify/")
    assert verify.status_code == 200
    assert verify.data["finalized_at"] is not None

    report = RadiologyReport.objects.get(pk=report_id)
    report.impression = "Tampered"
    with pytest.raises(ValueError):
        report.save()
    report.refresh_from_db()
    assert report.impression == "Normal"


@pytest.mark.django_db
def test_radiology_technician_can_create_report_but_cannot_verify(radiology_technician_client, radiology_order):
    create = radiology_technician_client.post("/api/v1/radiology/reports/", {
        "radiology_order": radiology_order.id, "findings": "Pending review", "impression": "Pending",
    }, format="json")
    assert create.status_code == 201

    verify = radiology_technician_client.post(f"/api/v1/radiology/reports/{create.data['id']}/verify/")
    assert verify.status_code == 403


# --- RBAC: whole-ViewSet clinical-detail gate, receptionist blocked ------

@pytest.mark.django_db
def test_receptionist_is_blocked_from_radiology_orders_and_reports(receptionist_client_radiology, radiology_order):
    assert receptionist_client_radiology.get("/api/v1/radiology/orders/").status_code == 403
    assert receptionist_client_radiology.get("/api/v1/radiology/reports/").status_code == 403


@pytest.mark.django_db
def test_radiology_procedure_catalogue_is_visible_to_receptionist(receptionist_client_radiology, procedure):
    response = receptionist_client_radiology.get("/api/v1/radiology/procedures/")
    assert response.status_code == 200


# --- Tenant isolation ------------------------------------------------------

@pytest.mark.django_db
def test_radiology_order_isolation(auth_client, other_hospital, other_department):
    other_patient = Patient.objects.create(hospital=other_hospital, first_name="Theirs", mobile="9000000099")
    other_procedure = RadiologyProcedure.objects.create(hospital=other_hospital, name="MRI Brain", modality=RadiologyProcedure.Modality.MRI)
    theirs = RadiologyOrder.objects.create(hospital=other_hospital, patient=other_patient, procedure=other_procedure)

    assert auth_client.get(f"/api/v1/radiology/orders/{theirs.id}/").status_code == 404
