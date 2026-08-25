from datetime import date

import pytest

from apps.accounts.models import User
from apps.billing.models import Bill
from apps.ipd.services import admit_patient
from apps.patients.models import Patient
from apps.saas_admin.models import SupportTicket, TenantInvoice, TenantSubscription, TenantUsageSnapshot
from apps.saas_admin.services import compute_tenant_usage, platform_analytics_snapshot
from apps.saas_admin.tasks import compute_monthly_tenant_usage


@pytest.fixture
def subscription(hospital):
    return TenantSubscription.objects.create(hospital=hospital, tier=TenantSubscription.Tier.PRO, max_staff_users=5, started_at=date(2026, 1, 1))


# --- IsSaaSAdmin gating ------------------------------------------------

@pytest.mark.django_db
def test_regular_user_is_blocked_from_saas_admin_subscriptions(auth_client):
    assert auth_client.get("/api/v1/saas-admin/subscriptions/").status_code == 403


@pytest.mark.django_db
def test_plain_staff_user_without_is_saas_admin_is_blocked(api_client, staff_user):
    """is_staff alone (the old, overloaded meaning of "platform ops") is
    not enough — IsSaaSAdmin is deliberately narrower, see its docstring."""
    api_client.force_authenticate(user=staff_user)
    assert api_client.get("/api/v1/saas-admin/subscriptions/").status_code == 403


@pytest.mark.django_db
def test_saas_admin_can_list_subscriptions_across_hospitals(saas_admin_client, subscription):
    response = saas_admin_client.get("/api/v1/saas-admin/subscriptions/")
    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert subscription.id in ids


@pytest.mark.django_db
def test_saas_admin_is_saas_admin_forces_is_staff_true():
    user = User.objects.create_user(email="new-saas-admin@polynexus.in", password="x", is_saas_admin=True)
    assert user.is_staff is True


# --- TenantSubscription / TenantInvoice CRUD ---------------------------

@pytest.mark.django_db
def test_saas_admin_can_create_a_subscription_for_any_hospital(saas_admin_client, hospital):
    response = saas_admin_client.post("/api/v1/saas-admin/subscriptions/", {
        "hospital": str(hospital.id), "tier": "enterprise", "max_staff_users": 50, "started_at": "2026-01-01",
    }, format="json")
    assert response.status_code == 201
    assert TenantSubscription.objects.get(hospital=hospital).tier == "enterprise"


@pytest.mark.django_db
def test_saas_admin_invoice_mark_paid_action(saas_admin_client, hospital, subscription):
    invoice = TenantInvoice.objects.create(
        hospital=hospital, subscription=subscription, invoice_number="INV-0001",
        billing_period_start=date(2026, 1, 1), billing_period_end=date(2026, 1, 31),
        amount=999, due_date=date(2026, 2, 5),
    )
    response = saas_admin_client.post(f"/api/v1/saas-admin/invoices/{invoice.id}/mark-paid/")
    assert response.status_code == 200
    invoice.refresh_from_db()
    assert invoice.status == TenantInvoice.Status.PAID
    assert invoice.paid_at is not None


@pytest.mark.django_db
def test_saas_admin_can_download_invoice_pdf(saas_admin_client, hospital, subscription):
    invoice = TenantInvoice.objects.create(
        hospital=hospital, subscription=subscription, invoice_number="INV-0002",
        billing_period_start=date(2026, 1, 1), billing_period_end=date(2026, 1, 31),
        amount=999, due_date=date(2026, 2, 5),
    )
    response = saas_admin_client.get(f"/api/v1/saas-admin/invoices/{invoice.id}/download/")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == f'attachment; filename="{invoice.invoice_number}.pdf"'
    body = b"".join(response.streaming_content) if response.streaming else response.content
    assert body.startswith(b"%PDF")
    assert len(body) > 500


@pytest.mark.django_db
def test_regular_user_cannot_download_invoice_pdf(auth_client, hospital, subscription):
    invoice = TenantInvoice.objects.create(
        hospital=hospital, subscription=subscription, invoice_number="INV-0003",
        billing_period_start=date(2026, 1, 1), billing_period_end=date(2026, 1, 31),
        amount=999, due_date=date(2026, 2, 5),
    )
    response = auth_client.get(f"/api/v1/saas-admin/invoices/{invoice.id}/download/")
    assert response.status_code == 403


@pytest.mark.django_db
def test_invoice_number_is_globally_unique_across_hospitals(hospital, other_hospital):
    """Invoice numbers come from one platform-wide FY sequence (Indian
    financial year, generate_invoice_number), not a per-hospital one — a
    real invoice ledger numbers consecutively across every customer, not
    per-customer. A raw duplicate insert (bypassing the generator) must
    still be rejected at the DB level."""
    from django.db import IntegrityError

    TenantInvoice.objects.create(
        hospital=hospital, invoice_number="INV-2025-26-00001",
        billing_period_start=date(2026, 1, 1), billing_period_end=date(2026, 1, 31),
        amount=100, due_date=date(2026, 2, 5),
    )
    with pytest.raises(IntegrityError):
        TenantInvoice.objects.create(
            hospital=other_hospital, invoice_number="INV-2025-26-00001",
            billing_period_start=date(2026, 1, 1), billing_period_end=date(2026, 1, 31),
            amount=200, due_date=date(2026, 2, 5),
        )


# --- generate_invoice_number / current_financial_year --------------------

@pytest.mark.django_db
def test_generate_invoice_number_format_and_sequence():
    from apps.saas_admin.services import generate_invoice_number

    first = generate_invoice_number(today=date(2026, 6, 15))
    second = generate_invoice_number(today=date(2026, 6, 15))

    assert first.startswith("INV-2026-27-")
    assert first == "INV-2026-27-00001"
    assert second == "INV-2026-27-00002"


def test_current_financial_year_boundary():
    from apps.saas_admin.services import current_financial_year

    assert current_financial_year(date(2026, 3, 31)) == "2025-26"  # last day of FY 2025-26
    assert current_financial_year(date(2026, 4, 1)) == "2026-27"   # FY rolls over on 1 April
    assert current_financial_year(date(2026, 12, 25)) == "2026-27"


@pytest.mark.django_db
def test_generate_invoice_number_resets_across_financial_years():
    from apps.saas_admin.services import generate_invoice_number

    last_of_fy = generate_invoice_number(today=date(2026, 3, 31))
    first_of_next_fy = generate_invoice_number(today=date(2026, 4, 1))

    assert last_of_fy == "INV-2025-26-00001"
    assert first_of_next_fy == "INV-2026-27-00001"  # independent counter, not continuing 00002


@pytest.mark.django_db
def test_saas_admin_create_invoice_ignores_client_supplied_invoice_number(saas_admin_client, hospital, subscription):
    """invoice_number is read-only on the serializer — the server always
    generates it (TenantInvoiceViewSet.perform_create), regardless of
    whatever the client sends."""
    response = saas_admin_client.post("/api/v1/saas-admin/invoices/", {
        "hospital": str(hospital.id), "subscription": subscription.id,
        "invoice_number": "CLIENT-SUPPLIED-VALUE",
        "billing_period_start": "2026-01-01", "billing_period_end": "2026-01-31",
        "amount": "999.00", "due_date": "2026-02-05",
    }, format="json")

    assert response.status_code == 201
    assert response.data["invoice_number"] != "CLIENT-SUPPLIED-VALUE"
    assert response.data["invoice_number"].startswith("INV-")
    invoice = TenantInvoice.objects.get(pk=response.data["id"])
    assert invoice.invoice_number == response.data["invoice_number"]


# --- SupportTicket: hospital-side create, SaaS-side triage --------------

@pytest.mark.django_db
def test_hospital_user_can_raise_a_support_ticket(auth_client, user):
    response = auth_client.post("/api/v1/support-tickets/", {
        "subject": "Billing looks wrong", "description": "Our invoice total doesn't match.", "category": "billing",
    }, format="json")
    assert response.status_code == 201
    ticket = SupportTicket.objects.get(pk=response.data["id"])
    assert ticket.hospital_id == user.hospital_id
    assert ticket.raised_by_id == user.id
    assert ticket.status == SupportTicket.Status.OPEN


@pytest.mark.django_db
def test_hospital_user_cannot_set_status_directly(auth_client, user):
    response = auth_client.post("/api/v1/support-tickets/", {
        "subject": "X", "description": "Y", "status": "resolved",
    }, format="json")
    assert response.status_code == 201
    ticket = SupportTicket.objects.get(pk=response.data["id"])
    assert ticket.status == SupportTicket.Status.OPEN  # status is read-only on this serializer, ignored


@pytest.mark.django_db
def test_hospital_user_only_sees_their_own_hospitals_tickets(auth_client, user, other_hospital):
    SupportTicket.objects.create(hospital=other_hospital, subject="Not mine", description="...")
    response = auth_client.get("/api/v1/support-tickets/")
    subjects = {row["subject"] for row in response.data["results"]}
    assert "Not mine" not in subjects


@pytest.mark.django_db
def test_regular_user_cannot_see_saas_admin_ticket_endpoint(auth_client):
    assert auth_client.get("/api/v1/saas-admin/tickets/").status_code == 403


@pytest.mark.django_db
def test_saas_admin_sees_tickets_across_every_hospital(saas_admin_client, hospital, other_hospital):
    SupportTicket.objects.create(hospital=hospital, subject="From hospital A", description="...")
    SupportTicket.objects.create(hospital=other_hospital, subject="From hospital B", description="...")

    response = saas_admin_client.get("/api/v1/saas-admin/tickets/")
    subjects = {row["subject"] for row in response.data["results"]}
    assert {"From hospital A", "From hospital B"} <= subjects


@pytest.mark.django_db
def test_saas_admin_can_resolve_a_ticket(saas_admin_client, hospital):
    ticket = SupportTicket.objects.create(hospital=hospital, subject="Bug", description="...")
    response = saas_admin_client.post(f"/api/v1/saas-admin/tickets/{ticket.id}/resolve/", {
        "resolution_notes": "Fixed in the next release.",
    }, format="json")
    assert response.status_code == 200
    ticket.refresh_from_db()
    assert ticket.status == SupportTicket.Status.RESOLVED
    assert ticket.resolution_notes == "Fixed in the next release."
    assert ticket.resolved_at is not None


@pytest.mark.django_db
def test_saas_admin_can_assign_a_ticket_to_another_saas_admin(saas_admin_client, hospital, saas_admin_user):
    other_admin = User.objects.create_user(email="second-saas-admin@polynexus.in", password="x", is_saas_admin=True)
    ticket = SupportTicket.objects.create(hospital=hospital, subject="Bug", description="...")

    response = saas_admin_client.post(f"/api/v1/saas-admin/tickets/{ticket.id}/assign/", {"assigned_to": other_admin.id}, format="json")

    assert response.status_code == 200
    ticket.refresh_from_db()
    assert ticket.assigned_to_id == other_admin.id
    assert ticket.status == SupportTicket.Status.IN_PROGRESS


@pytest.mark.django_db
def test_assign_rejects_a_non_saas_admin_target(saas_admin_client, hospital, user):
    ticket = SupportTicket.objects.create(hospital=hospital, subject="Bug", description="...")
    response = saas_admin_client.post(f"/api/v1/saas-admin/tickets/{ticket.id}/assign/", {"assigned_to": user.id}, format="json")
    assert response.status_code == 400


# --- Platform analytics --------------------------------------------------

@pytest.mark.django_db
def test_platform_analytics_counts_are_not_narrowed_by_ambient_tenant_context(hospital, other_hospital, department, other_department):
    """The regression this guards: services.platform_analytics_snapshot
    must use .unscoped()/Hospital.objects.all() explicitly rather than
    the bare TenantManager default, so a stray tenant_context() (or a
    future middleware change resolving one from the SaaS admin's own
    request) can never quietly narrow platform-wide numbers to one
    hospital."""
    from apps.core.tenancy import tenant_context

    Patient.objects.create(hospital=hospital, first_name="A", mobile="9000000010")
    Patient.objects.create(hospital=other_hospital, first_name="B", mobile="9000000011")

    with tenant_context(hospital.id):
        snapshot = platform_analytics_snapshot()

    assert snapshot["total_patients"] == 2
    assert snapshot["total_hospitals"] == 2


@pytest.mark.django_db
def test_platform_analytics_endpoint_rejects_a_regular_user(auth_client):
    assert auth_client.get("/api/v1/saas-admin/analytics/").status_code == 403


@pytest.mark.django_db
def test_platform_analytics_endpoint_allows_a_saas_admin(saas_admin_client):
    assert saas_admin_client.get("/api/v1/saas-admin/analytics/").status_code == 200


@pytest.mark.django_db
def test_platform_analytics_module_adoption_percentage(hospital, other_hospital):
    hospital.enabled_modules = ["pharmacy", "billing"]
    hospital.save(update_fields=["enabled_modules"])
    other_hospital.enabled_modules = ["pharmacy"]
    other_hospital.save(update_fields=["enabled_modules"])

    snapshot = platform_analytics_snapshot()

    assert snapshot["module_adoption_percent"]["pharmacy"] == 100.0
    assert snapshot["module_adoption_percent"]["billing"] == 50.0
    assert snapshot["module_adoption_percent"]["hr"] == 0.0


# --- Usage metering -------------------------------------------------------

@pytest.mark.django_db
def test_compute_tenant_usage_counts_patients_and_bills_in_the_period(hospital, department):
    from apps.appointments.models import Doctor
    from apps.facilities.models import Bed, Room, Ward

    Patient.objects.create(hospital=hospital, first_name="InPeriod", mobile="9000000020")

    doctor = Doctor.objects.create(hospital=hospital, department=department, name="Dr. Usage")
    patient = Patient.objects.create(hospital=hospital, first_name="Billed", mobile="9000000021")
    ward = Ward.objects.create(hospital=hospital, name="Usage Ward")
    room = Room.objects.create(hospital=hospital, ward=ward, room_number="1")
    bed = Bed.objects.create(hospital=hospital, room=room, bed_number="A")
    admit_patient(hospital=hospital, patient=patient, admitting_doctor=doctor, bed=bed)
    Bill.objects.create(hospital=hospital, patient=patient, net_amount=500)

    period_start, period_end = date(2020, 1, 1), date(2030, 1, 1)
    metrics = compute_tenant_usage(hospital, period_start, period_end)

    assert metrics["patients_registered_count"] == 2
    assert metrics["bills_generated_count"] == 1
    assert metrics["active_staff_count"] >= 0
    assert metrics["storage_bytes_used"] == 0  # no Document rows created in this test


@pytest.mark.django_db
def test_compute_monthly_tenant_usage_task_creates_one_snapshot_per_active_hospital(hospital, other_hospital):
    other_hospital.is_active = False
    other_hospital.save(update_fields=["is_active"])

    created = compute_monthly_tenant_usage()

    assert created == 1
    assert TenantUsageSnapshot.objects.filter(hospital=hospital).exists()
    assert not TenantUsageSnapshot.objects.filter(hospital=other_hospital).exists()


@pytest.mark.django_db
def test_usage_snapshot_endpoint_is_read_only_and_saas_admin_gated(saas_admin_client, hospital):
    snapshot = TenantUsageSnapshot.objects.create(hospital=hospital, period_start=date(2026, 1, 1), period_end=date(2026, 2, 1))
    response = saas_admin_client.post("/api/v1/saas-admin/usage-snapshots/", {"hospital": str(hospital.id)}, format="json")
    assert response.status_code == 405
    assert saas_admin_client.get(f"/api/v1/saas-admin/usage-snapshots/{snapshot.id}/").status_code == 200


# --- Staff-seat-limit enforcement (Part 4) --------------------------------

@pytest.mark.django_db
def test_user_creation_is_blocked_once_the_subscriptions_staff_limit_is_reached(auth_client, hospital, subscription):
    subscription.max_staff_users = 1
    subscription.save(update_fields=["max_staff_users"])
    # `user` (the auth_client fixture's actor) already counts as the 1st active staff user.

    response = auth_client.post("/api/v1/users/", {"email": "second-user@test-hospital.example"}, format="json")

    assert response.status_code == 400
    assert not User.objects.filter(email="second-user@test-hospital.example").exists()


@pytest.mark.django_db
def test_user_creation_succeeds_under_the_staff_limit(auth_client, subscription):
    subscription.max_staff_users = 5
    subscription.save(update_fields=["max_staff_users"])

    response = auth_client.post("/api/v1/users/", {"email": "third-user@test-hospital.example"}, format="json")

    assert response.status_code == 201


@pytest.mark.django_db
def test_user_creation_is_unaffected_when_the_hospital_has_no_subscription(auth_client):
    response = auth_client.post("/api/v1/users/", {"email": "fourth-user@test-hospital.example"}, format="json")
    assert response.status_code == 201
