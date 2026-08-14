from contextlib import suppress

import pytest

from apps.facilities.models import Bed, Room, Ward


def _teardown(instance):
    with suppress(Exception):
        instance.delete()


@pytest.fixture
def ward(hospital):
    created = Ward.objects.create(hospital=hospital, name="General Ward A")
    yield created
    _teardown(created)


@pytest.fixture
def other_ward(other_hospital):
    created = Ward.objects.create(hospital=other_hospital, name="General Ward A")
    yield created
    _teardown(created)


@pytest.fixture
def room(hospital, ward):
    created = Room.objects.create(hospital=hospital, ward=ward, room_number="101")
    yield created
    _teardown(created)


@pytest.fixture
def bed(hospital, room):
    created = Bed.objects.create(hospital=hospital, room=room, bed_number="A")
    yield created
    _teardown(created)


# --- Ward/Room/Bed: model CRUD ---------------------------------------------

@pytest.mark.django_db
def test_ward_model_create_with_required_fields_only(hospital):
    ward = Ward.objects.create(hospital=hospital, name="ICU")
    assert ward.pk is not None
    assert ward.ward_type == Ward.WardType.GENERAL
    assert ward.is_active is True
    _teardown(ward)


@pytest.mark.django_db
def test_ward_name_is_unique_per_hospital_not_globally(hospital, other_hospital):
    Ward.objects.create(hospital=hospital, name="ICU")
    same_name_other_hospital = Ward.objects.create(hospital=other_hospital, name="ICU")
    assert same_name_other_hospital.pk is not None
    _teardown(same_name_other_hospital)


@pytest.mark.django_db
def test_room_belongs_to_a_ward_and_room_number_is_unique_per_ward(hospital, ward):
    room = Room.objects.create(hospital=hospital, ward=ward, room_number="201")
    assert room.pk is not None
    with pytest.raises(Exception):
        Room.objects.create(hospital=hospital, ward=ward, room_number="201")
    _teardown(room)


@pytest.mark.django_db
def test_bed_defaults_to_available_status(hospital, room):
    bed = Bed.objects.create(hospital=hospital, room=room, bed_number="A")
    assert bed.status == Bed.Status.AVAILABLE
    _teardown(bed)


# --- Ward/Room/Bed: API CRUD + tenant isolation -----------------------------

@pytest.mark.django_db
def test_ward_api_create(auth_client, hospital):
    response = auth_client.post("/api/v1/facilities/wards/", {"name": "Maternity Ward"}, format="json")
    assert response.status_code == 201
    assert Ward.objects.get(pk=response.data["id"]).hospital_id == hospital.id


@pytest.mark.django_db
def test_ward_list_only_returns_the_authenticated_users_hospital_data(auth_client, ward, other_ward):
    response = auth_client.get("/api/v1/facilities/wards/")
    ids = [w["id"] for w in response.data["results"]]
    assert ward.id in ids
    assert other_ward.id not in ids


@pytest.mark.django_db
def test_ward_retrieve_404s_for_another_hospitals_object(auth_client, other_ward):
    response = auth_client.get(f"/api/v1/facilities/wards/{other_ward.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_bed_api_create_and_status_update(auth_client, room):
    create = auth_client.post("/api/v1/facilities/beds/", {"room": room.id, "bed_number": "B", "bed_type": "icu"}, format="json")
    assert create.status_code == 201
    bed_id = create.data["id"]

    update = auth_client.patch(f"/api/v1/facilities/beds/{bed_id}/", {"status": "occupied"}, format="json")
    assert update.status_code == 200
    assert Bed.objects.get(pk=bed_id).status == Bed.Status.OCCUPIED
