import datetime
from contextlib import suppress
from decimal import Decimal

import pytest

from apps.packages.models import Campaign, CampRegistration, HealthPackage


def _teardown(instance):
    """Mirrors Backend/conftest.py's `_teardown` — explicit, best-effort
    delete on top of pytest-django's implicit per-test transaction
    rollback. See conftest.py for the full rationale."""
    with suppress(Exception):
        instance.delete()


@pytest.fixture
def campaign(hospital):
    created = Campaign.objects.create(hospital=hospital, name="Diwali Health Camp", start_date=datetime.date(2026, 1, 5))
    yield created
    _teardown(created)


@pytest.fixture
def other_campaign(other_hospital):
    created = Campaign.objects.create(hospital=other_hospital, name="Not Mine Camp", start_date=datetime.date(2026, 1, 5))
    yield created
    _teardown(created)


# --- HealthPackage: model-level CRUD --------------------------------------


@pytest.mark.django_db
def test_health_package_model_create_with_required_fields_only(hospital):
    package = HealthPackage.objects.create(hospital=hospital, name="Full Body Checkup", code="FB01", price=Decimal("2500.00"))
    assert package.pk is not None
    assert package.category == HealthPackage.Category.FULL_BODY
    assert package.is_active is True
    assert package.included_tests == []
    _teardown(package)


@pytest.mark.django_db
def test_health_package_model_create_with_full_fields(hospital):
    package = HealthPackage.objects.create(
        hospital=hospital, name="Cardiac Special", code="CARD01", category=HealthPackage.Category.CARDIAC,
        price=Decimal("4999.99"), description="ECG + TMT + Lipid Profile", included_tests=["ecg", "tmt", "lipid"],
        is_active=False,
    )
    assert package.category == HealthPackage.Category.CARDIAC
    assert package.included_tests == ["ecg", "tmt", "lipid"]
    assert package.is_active is False
    _teardown(package)


@pytest.mark.django_db
def test_health_package_model_retrieve(hospital):
    package = HealthPackage.objects.create(hospital=hospital, name="Ortho Package", code="ORTHO01", price=Decimal("1999.00"))
    fetched = HealthPackage.objects.get(pk=package.pk)
    assert fetched.name == "Ortho Package"
    _teardown(package)


@pytest.mark.django_db
def test_health_package_model_update(hospital):
    package = HealthPackage.objects.create(hospital=hospital, name="Maternity Basic", code="MAT01", price=Decimal("3000.00"))
    package.price = Decimal("3500.00")
    package.is_active = False
    package.save()
    package.refresh_from_db()
    assert package.price == Decimal("3500.00")
    assert package.is_active is False
    _teardown(package)


@pytest.mark.django_db
def test_health_package_model_delete(hospital):
    package = HealthPackage.objects.create(hospital=hospital, name="Delete Me", code="DEL01", price=Decimal("500.00"))
    package_id = package.pk
    package.delete()
    assert not HealthPackage.objects.filter(pk=package_id).exists()


@pytest.mark.django_db
def test_health_package_code_has_no_db_level_uniqueness_constraint(hospital):
    """`HealthPackage.code` looks unique but there is no Meta.constraints
    (or field-level unique=True) enforcing it — a duplicate code must not
    raise IntegrityError."""
    first = HealthPackage.objects.create(hospital=hospital, name="A", code="DUPE", price=Decimal("100.00"))
    second = HealthPackage.objects.create(hospital=hospital, name="B", code="DUPE", price=Decimal("200.00"))
    assert HealthPackage.objects.filter(code="DUPE").count() == 2
    _teardown(first)
    _teardown(second)


# --- Campaign: model-level CRUD -------------------------------------------


@pytest.mark.django_db
def test_campaign_model_create_with_required_fields_only(hospital):
    camp = Campaign.objects.create(hospital=hospital, name="Winter Health Camp", start_date=datetime.date(2026, 2, 1))
    assert camp.pk is not None
    assert camp.campaign_type == Campaign.CampaignType.HEALTH_CAMP
    assert camp.status == Campaign.Status.ACTIVE
    assert camp.budget == Decimal("0.00")
    _teardown(camp)


@pytest.mark.django_db
def test_campaign_model_create_with_full_fields(hospital):
    camp = Campaign.objects.create(
        hospital=hospital, name="Meta Ad Push", campaign_type=Campaign.CampaignType.DIGITAL_AD,
        budget=Decimal("50000.00"), actual_spend=Decimal("32000.00"), status=Campaign.Status.COMPLETED,
        start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 31),
    )
    assert camp.campaign_type == Campaign.CampaignType.DIGITAL_AD
    assert camp.actual_spend == Decimal("32000.00")
    assert camp.end_date == datetime.date(2026, 1, 31)
    _teardown(camp)


@pytest.mark.django_db
def test_campaign_model_update(campaign):
    campaign.status = Campaign.Status.COMPLETED
    campaign.actual_spend = Decimal("12000.00")
    campaign.save()
    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.COMPLETED
    assert campaign.actual_spend == Decimal("12000.00")


@pytest.mark.django_db
def test_campaign_model_delete(hospital):
    camp = Campaign.objects.create(hospital=hospital, name="Delete Me", start_date=datetime.date(2026, 1, 1))
    camp_id = camp.pk
    camp.delete()
    assert not Campaign.objects.filter(pk=camp_id).exists()


# --- CampRegistration: model-level CRUD ------------------------------------


@pytest.mark.django_db
def test_camp_registration_model_create_with_required_fields_only(campaign):
    reg = CampRegistration.objects.create(hospital=campaign.hospital, campaign=campaign, patient_name="Asha Patil", mobile="9822011111")
    assert reg.pk is not None
    assert reg.stage == CampRegistration.FunnelStage.REGISTERED
    assert reg.revenue_generated == Decimal("0.00")
    _teardown(reg)


@pytest.mark.django_db
def test_camp_registration_model_create_with_full_fields(campaign):
    reg = CampRegistration.objects.create(
        hospital=campaign.hospital, campaign=campaign, patient_name="Rohit Sharma", mobile="9822022222",
        stage=CampRegistration.FunnelStage.IPD_CONVERTED, revenue_generated=Decimal("45000.00"),
    )
    assert reg.stage == CampRegistration.FunnelStage.IPD_CONVERTED
    assert reg.revenue_generated == Decimal("45000.00")
    _teardown(reg)


@pytest.mark.django_db
def test_camp_registration_model_update(campaign):
    reg = CampRegistration.objects.create(hospital=campaign.hospital, campaign=campaign, patient_name="Test", mobile="9800000000")
    reg.stage = CampRegistration.FunnelStage.OPD_CONVERTED
    reg.revenue_generated = Decimal("1500.00")
    reg.save()
    reg.refresh_from_db()
    assert reg.stage == CampRegistration.FunnelStage.OPD_CONVERTED
    assert reg.revenue_generated == Decimal("1500.00")
    _teardown(reg)


@pytest.mark.django_db
def test_camp_registration_model_delete(campaign):
    reg = CampRegistration.objects.create(hospital=campaign.hospital, campaign=campaign, patient_name="Delete", mobile="9800000001")
    reg_id = reg.pk
    reg.delete()
    assert not CampRegistration.objects.filter(pk=reg_id).exists()


# --- HealthPackageViewSet: API-level CRUD ----------------------------------


@pytest.mark.django_db
def test_health_package_api_create_with_required_fields_only(auth_client, hospital):
    response = auth_client.post("/api/v1/packages/catalog/", {"name": "Basic Checkup", "code": "BC01", "price": "999.00"}, format="json")
    assert response.status_code == 201
    created = HealthPackage.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id
    assert created.name == "Basic Checkup"
    assert created.price == Decimal("999.00")


@pytest.mark.django_db
def test_health_package_api_list_returns_created_package(auth_client, hospital):
    package = HealthPackage.objects.create(hospital=hospital, name="Listed Package", code="LP01", price=Decimal("100.00"))
    response = auth_client.get("/api/v1/packages/catalog/")
    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert package.id in ids


@pytest.mark.django_db
def test_health_package_api_retrieve(auth_client, hospital):
    package = HealthPackage.objects.create(hospital=hospital, name="Retrieve Me", code="RM01", price=Decimal("100.00"))
    response = auth_client.get(f"/api/v1/packages/catalog/{package.id}/")
    assert response.status_code == 200
    assert response.data["name"] == "Retrieve Me"


@pytest.mark.django_db
def test_health_package_api_update(auth_client, hospital):
    package = HealthPackage.objects.create(hospital=hospital, name="Update Me", code="UM01", price=Decimal("100.00"))
    response = auth_client.patch(f"/api/v1/packages/catalog/{package.id}/", {"price": "150.00", "is_active": False}, format="json")
    assert response.status_code == 200
    package.refresh_from_db()
    assert package.price == Decimal("150.00")
    assert package.is_active is False


@pytest.mark.django_db
def test_health_package_api_delete(auth_client, hospital):
    package = HealthPackage.objects.create(hospital=hospital, name="Delete Via API", code="DVA01", price=Decimal("100.00"))
    response = auth_client.delete(f"/api/v1/packages/catalog/{package.id}/")
    assert response.status_code == 204
    assert not HealthPackage.objects.filter(pk=package.id).exists()


# --- CampaignViewSet: API-level CRUD ---------------------------------------


@pytest.mark.django_db
def test_campaign_api_create_with_required_fields_only(auth_client, hospital):
    response = auth_client.post("/api/v1/packages/campaigns/", {"name": "New Campaign", "start_date": "2026-03-01"}, format="json")
    assert response.status_code == 201
    created = Campaign.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id
    assert created.name == "New Campaign"


@pytest.mark.django_db
def test_campaign_api_list(auth_client, campaign):
    response = auth_client.get("/api/v1/packages/campaigns/")
    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert campaign.id in ids


@pytest.mark.django_db
def test_campaign_api_retrieve_includes_annotated_totals(auth_client, campaign):
    CampRegistration.objects.create(hospital=campaign.hospital, campaign=campaign, patient_name="A", mobile="1", revenue_generated=Decimal("1000.00"))
    CampRegistration.objects.create(hospital=campaign.hospital, campaign=campaign, patient_name="B", mobile="2", revenue_generated=Decimal("2500.00"))

    response = auth_client.get(f"/api/v1/packages/campaigns/{campaign.id}/")

    assert response.status_code == 200
    assert response.data["total_registrations"] == 2
    assert Decimal(response.data["total_revenue_generated"]) == Decimal("3500.00")


@pytest.mark.django_db
def test_campaign_api_update(auth_client, campaign):
    response = auth_client.patch(f"/api/v1/packages/campaigns/{campaign.id}/", {"status": Campaign.Status.COMPLETED}, format="json")
    assert response.status_code == 200
    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.COMPLETED


@pytest.mark.django_db
def test_campaign_api_delete(auth_client, campaign):
    response = auth_client.delete(f"/api/v1/packages/campaigns/{campaign.id}/")
    assert response.status_code == 204
    assert not Campaign.objects.filter(pk=campaign.id).exists()


# --- CampRegistrationViewSet: API-level CRUD -------------------------------


@pytest.mark.django_db
def test_camp_registration_api_create_with_required_fields_only(auth_client, hospital, campaign):
    response = auth_client.post(
        "/api/v1/packages/registrations/",
        {"campaign": campaign.id, "patient_name": "New Registrant", "mobile": "9811122233"},
        format="json",
    )
    assert response.status_code == 201
    created = CampRegistration.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id
    assert created.campaign_id == campaign.id
    assert response.data["campaign_name"] == campaign.name


@pytest.mark.django_db
def test_camp_registration_api_list(auth_client, campaign):
    reg = CampRegistration.objects.create(hospital=campaign.hospital, campaign=campaign, patient_name="Listed", mobile="9800000002")
    response = auth_client.get("/api/v1/packages/registrations/")
    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert reg.id in ids


@pytest.mark.django_db
def test_camp_registration_api_retrieve(auth_client, campaign):
    reg = CampRegistration.objects.create(hospital=campaign.hospital, campaign=campaign, patient_name="Retrieve Me", mobile="9800000003")
    response = auth_client.get(f"/api/v1/packages/registrations/{reg.id}/")
    assert response.status_code == 200
    assert response.data["patient_name"] == "Retrieve Me"


@pytest.mark.django_db
def test_camp_registration_api_update(auth_client, campaign):
    reg = CampRegistration.objects.create(hospital=campaign.hospital, campaign=campaign, patient_name="Update Me", mobile="9800000004")
    response = auth_client.patch(f"/api/v1/packages/registrations/{reg.id}/", {"stage": CampRegistration.FunnelStage.ATTENDED}, format="json")
    assert response.status_code == 200
    reg.refresh_from_db()
    assert reg.stage == CampRegistration.FunnelStage.ATTENDED


@pytest.mark.django_db
def test_camp_registration_api_delete(auth_client, campaign):
    reg = CampRegistration.objects.create(hospital=campaign.hospital, campaign=campaign, patient_name="Delete Me", mobile="9800000005")
    response = auth_client.delete(f"/api/v1/packages/registrations/{reg.id}/")
    assert response.status_code == 204
    assert not CampRegistration.objects.filter(pk=reg.id).exists()


# --- Cross-tenant isolation -------------------------------------------------
#
# All three viewsets here use TenantScopedViewSetMixin. CampaignViewSet is
# the interesting case: its get_queryset() calls `super().get_queryset()`
# (the mixin's fixed, per-request-filtered queryset) and then `.annotate(...)`
# on top of it — so these tests also confirm the fix survives being wrapped
# by an annotate() call, not just the plain case.


@pytest.mark.django_db
def test_health_package_list_only_returns_the_authenticated_users_hospital_data(auth_client, hospital, other_hospital):
    mine = HealthPackage.objects.create(hospital=hospital, name="Mine", code="M1", price=Decimal("100.00"))
    HealthPackage.objects.create(hospital=other_hospital, name="NotMine", code="N1", price=Decimal("200.00"))

    response = auth_client.get("/api/v1/packages/catalog/")

    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert ids == {mine.id}


@pytest.mark.django_db
def test_health_package_retrieve_404s_for_another_hospitals_object(auth_client, other_hospital):
    theirs = HealthPackage.objects.create(hospital=other_hospital, name="NotMine", code="N1", price=Decimal("200.00"))
    response = auth_client.get(f"/api/v1/packages/catalog/{theirs.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_health_package_update_cannot_modify_another_hospitals_object(auth_client, other_hospital):
    theirs = HealthPackage.objects.create(hospital=other_hospital, name="NotMine", code="N1", price=Decimal("200.00"))
    response = auth_client.patch(f"/api/v1/packages/catalog/{theirs.id}/", {"name": "Hijacked"}, format="json")
    assert response.status_code == 404
    theirs.refresh_from_db()
    assert theirs.name == "NotMine"


@pytest.mark.django_db
def test_health_package_destroy_cannot_delete_another_hospitals_object(auth_client, other_hospital):
    theirs = HealthPackage.objects.create(hospital=other_hospital, name="NotMine", code="N1", price=Decimal("200.00"))
    response = auth_client.delete(f"/api/v1/packages/catalog/{theirs.id}/")
    assert response.status_code == 404
    assert HealthPackage.objects.filter(pk=theirs.id).exists()


@pytest.mark.django_db
def test_campaign_list_only_returns_the_authenticated_users_hospital_data(auth_client, campaign, other_campaign):
    """The annotate()-wrapped get_queryset() case (see block comment above)."""
    response = auth_client.get("/api/v1/packages/campaigns/")
    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert ids == {campaign.id}


@pytest.mark.django_db
def test_campaign_retrieve_404s_for_another_hospitals_object(auth_client, other_campaign):
    response = auth_client.get(f"/api/v1/packages/campaigns/{other_campaign.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_campaign_update_cannot_modify_another_hospitals_object(auth_client, other_campaign):
    response = auth_client.patch(f"/api/v1/packages/campaigns/{other_campaign.id}/", {"name": "Hijacked"}, format="json")
    assert response.status_code == 404
    other_campaign.refresh_from_db()
    assert other_campaign.name == "Not Mine Camp"


@pytest.mark.django_db
def test_campaign_destroy_cannot_delete_another_hospitals_object(auth_client, other_campaign):
    response = auth_client.delete(f"/api/v1/packages/campaigns/{other_campaign.id}/")
    assert response.status_code == 404
    assert Campaign.objects.filter(pk=other_campaign.id).exists()


@pytest.mark.django_db
def test_camp_registration_list_only_returns_the_authenticated_users_hospital_data(auth_client, campaign, other_campaign):
    mine = CampRegistration.objects.create(hospital=campaign.hospital, campaign=campaign, patient_name="Mine", mobile="1")
    CampRegistration.objects.create(hospital=other_campaign.hospital, campaign=other_campaign, patient_name="NotMine", mobile="2")

    response = auth_client.get("/api/v1/packages/registrations/")

    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert ids == {mine.id}


@pytest.mark.django_db
def test_camp_registration_retrieve_404s_for_another_hospitals_object(auth_client, other_campaign):
    theirs = CampRegistration.objects.create(hospital=other_campaign.hospital, campaign=other_campaign, patient_name="NotMine", mobile="2")
    response = auth_client.get(f"/api/v1/packages/registrations/{theirs.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_camp_registration_update_cannot_modify_another_hospitals_object(auth_client, other_campaign):
    theirs = CampRegistration.objects.create(hospital=other_campaign.hospital, campaign=other_campaign, patient_name="NotMine", mobile="2")
    response = auth_client.patch(f"/api/v1/packages/registrations/{theirs.id}/", {"patient_name": "Hijacked"}, format="json")
    assert response.status_code == 404
    theirs.refresh_from_db()
    assert theirs.patient_name == "NotMine"


@pytest.mark.django_db
def test_camp_registration_destroy_cannot_delete_another_hospitals_object(auth_client, other_campaign):
    theirs = CampRegistration.objects.create(hospital=other_campaign.hospital, campaign=other_campaign, patient_name="NotMine", mobile="2")
    response = auth_client.delete(f"/api/v1/packages/registrations/{theirs.id}/")
    assert response.status_code == 404
    assert CampRegistration.objects.filter(pk=theirs.id).exists()


@pytest.mark.django_db
def test_create_still_stamps_the_requesting_users_hospital(auth_client, hospital):
    response = auth_client.post(
        "/api/v1/packages/catalog/",
        {"name": "Spoof Attempt", "code": "SP01", "price": "1.00", "hospital": "should-be-ignored"},
        format="json",
    )
    assert response.status_code == 201
    created = HealthPackage.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id
