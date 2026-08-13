import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.enquiries.models import Enquiry, EnquiryStageChange
from apps.enquiries.services import assign_enquiry, find_duplicates


@pytest.mark.django_db
def test_find_duplicates_matches_on_mobile(hospital):
    first = Enquiry.objects.create(hospital=hospital, name="Asha Patil", mobile="9876543210", source=Enquiry.Source.WEBSITE)

    duplicates = find_duplicates(hospital, mobile="9876543210")
    assert list(duplicates) == [first]


@pytest.mark.django_db
def test_second_enquiry_from_same_number_is_flagged_as_duplicate(hospital):
    first = Enquiry.objects.create(hospital=hospital, name="Asha Patil", mobile="9876543210", source=Enquiry.Source.WEBSITE)
    second = Enquiry.objects.create(hospital=hospital, name="Asha P.", mobile="9876543210", source=Enquiry.Source.IVR)

    second.refresh_from_db()
    assert second.duplicate_of_id == first.id


@pytest.mark.django_db
def test_assign_enquiry_picks_least_loaded_department_user(hospital, department, user):
    enquiry = Enquiry.objects.create(hospital=hospital, name="Ravi Kumar", mobile="9123456780", source=Enquiry.Source.WALK_IN, department=department)
    owner = assign_enquiry(enquiry)
    assert owner == user
    enquiry.refresh_from_db()
    assert enquiry.assigned_to_id == user.id


# --- Enquiry: model CRUD -----------------------------------------------

@pytest.mark.django_db
def test_enquiry_model_create_update_delete(hospital):
    enquiry = Enquiry.objects.create(hospital=hospital, name="Test Lead", mobile="9000011111", source=Enquiry.Source.WEBSITE)
    enquiry.notes = "Called back once"
    enquiry.save(update_fields=["notes"])
    enquiry.refresh_from_db()
    assert enquiry.notes == "Called back once"

    enquiry_id = enquiry.id
    enquiry.delete()
    assert not Enquiry.objects.filter(pk=enquiry_id).exists()


# --- Enquiry: API CRUD + the auto-assignment side effect ------------------

@pytest.mark.django_db
def test_enquiry_api_create_requires_name_mobile_source(auth_client, hospital):
    response = auth_client.post("/api/v1/enquiries/", {"name": "New Lead", "mobile": "9000022222", "source": "website"}, format="json")

    assert response.status_code == 201
    created = Enquiry.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id


@pytest.mark.django_db
def test_enquiry_created_via_api_is_auto_assigned_to_the_only_active_user(auth_client, user):
    """Real side effect from apps.enquiries.signals: assigned_to isn't left
    null just because the POST body didn't set it — the post_save signal
    auto-assigns the least-loaded active user in the hospital. A naive test
    asserting `assigned_to is None` after API creation would be wrong."""
    response = auth_client.post("/api/v1/enquiries/", {"name": "Auto Assign Test", "mobile": "9000033333", "source": "ivr"}, format="json")

    created = Enquiry.objects.get(pk=response.data["id"])
    assert created.assigned_to_id == user.id


@pytest.mark.django_db
def test_enquiry_api_update_and_delete(auth_client, hospital):
    enquiry = Enquiry.objects.create(hospital=hospital, name="X", mobile="9000044444", source=Enquiry.Source.OTHER)

    update = auth_client.patch(f"/api/v1/enquiries/{enquiry.id}/", {"urgency": "high"}, format="json")
    assert update.status_code == 200
    enquiry.refresh_from_db()
    assert enquiry.urgency == "high"

    delete = auth_client.delete(f"/api/v1/enquiries/{enquiry.id}/")
    assert delete.status_code == 204
    assert not Enquiry.objects.filter(pk=enquiry.id).exists()


@pytest.mark.django_db
def test_enquiry_isolation(auth_client, other_hospital):
    theirs = Enquiry.objects.create(hospital=other_hospital, name="Theirs", mobile="9000055555", source=Enquiry.Source.OTHER)

    assert auth_client.get(f"/api/v1/enquiries/{theirs.id}/").status_code == 404
    assert auth_client.patch(f"/api/v1/enquiries/{theirs.id}/", {"urgency": "high"}, format="json").status_code == 404
    assert auth_client.delete(f"/api/v1/enquiries/{theirs.id}/").status_code == 404
    ids = {row["id"] for row in auth_client.get("/api/v1/enquiries/").data["results"]}
    assert theirs.id not in ids


# --- move-stage / lose actions ----------------------------------------------

@pytest.mark.django_db
def test_move_stage_action_sets_sla_due_at_on_entering_an_open_stage(auth_client, hospital):
    enquiry = Enquiry.objects.create(hospital=hospital, name="X", mobile="9000066666", source=Enquiry.Source.OTHER, stage=Enquiry.Stage.NEW)
    assert enquiry.sla_due_at is None

    response = auth_client.post(f"/api/v1/enquiries/{enquiry.id}/move-stage/", {"stage": "contacted"}, format="json")

    assert response.status_code == 200
    enquiry.refresh_from_db()
    assert enquiry.stage == "contacted"
    assert enquiry.sla_due_at is not None
    assert EnquiryStageChange.objects.filter(enquiry=enquiry, from_stage="new", to_stage="contacted").exists()


@pytest.mark.django_db
def test_move_stage_action_is_a_noop_for_the_same_stage(auth_client, hospital):
    enquiry = Enquiry.objects.create(hospital=hospital, name="X", mobile="9000077777", source=Enquiry.Source.OTHER, stage=Enquiry.Stage.NEW)

    response = auth_client.post(f"/api/v1/enquiries/{enquiry.id}/move-stage/", {"stage": "new"}, format="json")

    assert response.status_code == 200
    assert not EnquiryStageChange.objects.filter(enquiry=enquiry).exists()


@pytest.mark.django_db
def test_lose_action_sets_lost_reason_and_moves_to_lost_stage(auth_client, hospital):
    enquiry = Enquiry.objects.create(hospital=hospital, name="X", mobile="9000088888", source=Enquiry.Source.OTHER)

    response = auth_client.post(f"/api/v1/enquiries/{enquiry.id}/lose/", {"lost_reason": "Chose another hospital"}, format="json")

    assert response.status_code == 200
    enquiry.refresh_from_db()
    assert enquiry.stage == Enquiry.Stage.LOST
    assert enquiry.lost_reason == "Chose another hospital"


@pytest.mark.django_db
def test_lose_action_requires_lost_reason(auth_client, hospital):
    enquiry = Enquiry.objects.create(hospital=hospital, name="X", mobile="9000099999", source=Enquiry.Source.OTHER)
    response = auth_client.post(f"/api/v1/enquiries/{enquiry.id}/lose/", {}, format="json")
    assert response.status_code == 400


# --- bulk-import: multipart CSV ---------------------------------------------

@pytest.mark.django_db
def test_bulk_import_creates_enquiries_from_a_valid_csv(auth_client, hospital):
    csv_content = "name,mobile,email,source,service_requested\r\nRavi Kumar,9111111111,,walk_in,OPD\r\nSeema Joshi,9222222222,seema@example.com,website,Cardiology\r\n"
    upload = SimpleUploadedFile("leads.csv", csv_content.encode("utf-8"), content_type="text/csv")

    response = auth_client.post("/api/v1/enquiries/bulk-import/", {"file": upload}, format="multipart")

    assert response.status_code == 201
    assert response.data["created"] == 2
    assert response.data["errors"] == []
    assert Enquiry.objects.filter(hospital=hospital, mobile="9111111111").exists()
    assert Enquiry.objects.filter(hospital=hospital, mobile="9222222222").exists()


@pytest.mark.django_db
def test_bulk_import_reports_row_errors_without_failing_the_whole_batch(auth_client, hospital):
    csv_content = "name,mobile,email,source,service_requested\r\nGood Row,9333333333,,walk_in,\r\n,9444444444,,walk_in,\r\n"
    upload = SimpleUploadedFile("leads.csv", csv_content.encode("utf-8"), content_type="text/csv")

    response = auth_client.post("/api/v1/enquiries/bulk-import/", {"file": upload}, format="multipart")

    assert response.status_code == 201
    assert response.data["created"] == 1
    assert len(response.data["errors"]) == 1
    assert response.data["errors"][0]["line"] == 3


@pytest.mark.django_db
def test_bulk_import_without_a_file_returns_400(auth_client):
    response = auth_client.post("/api/v1/enquiries/bulk-import/", {}, format="multipart")
    assert response.status_code == 400
