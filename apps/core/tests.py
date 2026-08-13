import pytest

from apps.core.models import Department
from apps.core.tenancy import tenant_context


@pytest.mark.django_db
def test_tenant_manager_scopes_queryset_to_current_hospital(hospital, other_hospital):
    Department.objects.create(hospital=hospital, name="OPD")
    Department.objects.create(hospital=other_hospital, name="IPD")

    with tenant_context(hospital.id):
        names = set(Department.objects.values_list("name", flat=True))
    assert names == {"OPD"}

    with tenant_context(other_hospital.id):
        names = set(Department.objects.values_list("name", flat=True))
    assert names == {"IPD"}


@pytest.mark.django_db
def test_tenant_manager_is_unscoped_outside_request_context(hospital, other_hospital):
    Department.objects.create(hospital=hospital, name="OPD")
    Department.objects.create(hospital=other_hospital, name="IPD")

    # No tenant_context set — management commands / migrations / Celery
    # beat need to see everything.
    assert Department.objects.count() == 2


# --- TenantScopedViewSetMixin.get_queryset regression coverage ---------
#
# The manager-level tests above were never the bug: TenantManager has
# always filtered correctly *when re-invoked per request*. The actual bug
# was that every ModelViewSet using TenantScopedViewSetMixin declared
# `queryset = Model.objects.all()` as a class attribute, which freezes an
# *unfiltered* queryset at import time (before any request/tenant context
# exists) — DRF's default get_queryset() then just clones that frozen
# queryset instead of re-querying, so list/retrieve/update/destroy leaked
# every hospital's data to every other hospital via the API, even though
# create was correctly scoped. These tests hit real endpoints from two
# different apps (appointments.Doctor, patients.Patient) through the real
# HTTP/DRF stack — not the manager directly — to prove the mixin fix holds
# for any viewset that uses it, not just one.


@pytest.mark.django_db
def test_list_endpoint_only_returns_the_authenticated_users_hospital_data(auth_client, hospital, other_hospital, department, other_department):
    from apps.appointments.models import Doctor

    mine = Doctor.objects.create(hospital=hospital, department=department, name="Mine")
    Doctor.objects.create(hospital=other_hospital, department=other_department, name="NotMine")

    response = auth_client.get("/api/v1/doctors/")

    assert response.status_code == 200
    names = {row["name"] for row in response.data["results"]}
    assert names == {"Mine"}
    ids = {row["id"] for row in response.data["results"]}
    assert ids == {mine.id}


@pytest.mark.django_db
def test_retrieve_endpoint_404s_for_another_hospitals_object(auth_client, other_hospital, other_department):
    from apps.appointments.models import Doctor

    theirs = Doctor.objects.create(hospital=other_hospital, department=other_department, name="NotMine")

    response = auth_client.get(f"/api/v1/doctors/{theirs.id}/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_update_endpoint_cannot_modify_another_hospitals_object(auth_client, other_hospital, other_department):
    from apps.appointments.models import Doctor

    theirs = Doctor.objects.create(hospital=other_hospital, department=other_department, name="NotMine")

    response = auth_client.patch(f"/api/v1/doctors/{theirs.id}/", {"name": "Hijacked"}, format="json")

    assert response.status_code == 404
    theirs.refresh_from_db()
    assert theirs.name == "NotMine"


@pytest.mark.django_db
def test_destroy_endpoint_cannot_delete_another_hospitals_object(auth_client, other_hospital, other_department):
    from apps.appointments.models import Doctor

    theirs = Doctor.objects.create(hospital=other_hospital, department=other_department, name="NotMine")

    response = auth_client.delete(f"/api/v1/doctors/{theirs.id}/")

    assert response.status_code == 404
    assert Doctor.objects.filter(pk=theirs.id).exists()


@pytest.mark.django_db
def test_isolation_holds_for_a_second_unrelated_app_too(auth_client, hospital, other_hospital):
    """Same class of bug, different app — patients.PatientViewSet — proving
    the fix in the shared mixin generalises rather than being coincidental
    to the Doctor test above."""
    from apps.patients.models import Patient

    mine = Patient.objects.create(hospital=hospital, first_name="Mine", mobile="9000000001")
    theirs = Patient.objects.create(hospital=other_hospital, first_name="NotMine", mobile="9000000002")

    list_response = auth_client.get("/api/v1/patients/")
    ids = {row["id"] for row in list_response.data["results"]}
    assert ids == {mine.id}

    retrieve_response = auth_client.get(f"/api/v1/patients/{theirs.id}/")
    assert retrieve_response.status_code == 404


@pytest.mark.django_db
def test_create_still_stamps_the_requesting_users_hospital(auth_client, hospital):
    """perform_create was never broken — confirm the fix didn't change
    that, and that a client can't spoof a different hospital in the body."""
    from apps.appointments.models import Doctor

    response = auth_client.post("/api/v1/doctors/", {"name": "New Doctor", "hospital": "should-be-ignored"}, format="json")

    assert response.status_code == 201
    created = Doctor.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id


@pytest.mark.django_db
def test_unauthenticated_request_is_rejected_not_unscoped(api_client):
    """Belt-and-suspenders: even if auth were somehow bypassed, an
    unauthenticated request must not fall through to an unscoped queryset
    (get_current_hospital_id() is None -> .none(), not .all())."""
    response = api_client.get("/api/v1/doctors/")
    assert response.status_code == 401


# --- RoleBasedModelPermissions --------------------------------------------
#
# Proves the permission class actually restricts something for a
# genuinely low-privilege role (Telephony Operator template — see
# apps.accounts.permission_templates), not just that the plumbing exists
# and happens to let everything through. `auth_client`'s role uses the
# "admin" template specifically so the hundreds of CRUD-mechanics tests
# elsewhere aren't testing permissions by accident; these are the tests
# that are actually about permissions.


@pytest.mark.django_db
def test_restricted_role_can_write_within_its_granted_apps(restricted_client, hospital, department):
    """Telephony Operator's template grants telephony + enquiries —
    confirm the permission class doesn't over-restrict a role's own
    apps."""
    response = restricted_client.post("/api/v1/enquiries/", {"name": "X", "mobile": "9000000000", "source": "ivr"}, format="json")
    assert response.status_code == 201


@pytest.mark.django_db
def test_restricted_role_cannot_create_a_patient(restricted_client):
    """Telephony Operator's template grants patients: ["view"] only — no
    add/change/delete."""
    response = restricted_client.post("/api/v1/patients/", {"first_name": "X", "mobile": "9000000001"}, format="json")
    assert response.status_code == 403


@pytest.mark.django_db
def test_restricted_role_can_still_list_and_retrieve_patients(restricted_client, hospital, department):
    """Read access is deliberately not gated by this permission class —
    every role needs to see records relevant to its screens."""
    from apps.patients.models import Patient
    patient = Patient.objects.create(hospital=hospital, first_name="Viewable", mobile="9000000002")

    assert restricted_client.get("/api/v1/patients/").status_code == 200
    assert restricted_client.get(f"/api/v1/patients/{patient.id}/").status_code == 200


@pytest.mark.django_db
def test_restricted_role_cannot_touch_an_app_outside_its_template_at_all(restricted_client, hospital, department):
    """tpa isn't in the Telephony Operator template at all."""
    from apps.patients.models import Patient
    from apps.tpa.models import TPACompany

    patient = Patient.objects.create(hospital=hospital, first_name="X", mobile="9000000003")
    tpa_company = TPACompany.objects.create(hospital=hospital, name="Star Health", code="STAR")

    response = restricted_client.post("/api/v1/tpa/pre-auth/", {
        "patient": patient.id, "tpa_company": tpa_company.id, "policy_number": "POL1", "claim_amount": "1000.00",
    }, format="json")
    assert response.status_code == 403


@pytest.mark.django_db
def test_restricted_role_cannot_update_or_delete_outside_its_template(restricted_client, hospital, department):
    from apps.patients.models import Patient
    patient = Patient.objects.create(hospital=hospital, first_name="X", mobile="9000000004")

    assert restricted_client.patch(f"/api/v1/patients/{patient.id}/", {"city": "Pune"}, format="json").status_code == 403
    assert restricted_client.delete(f"/api/v1/patients/{patient.id}/").status_code == 403
    patient.refresh_from_db()
    assert patient.city != "Pune"


@pytest.mark.django_db
def test_role_with_no_template_gets_no_default_permissions(hospital, department):
    """template="" (blank, the default) is a hand-built role — a hospital
    admin configuring one from scratch via Django admin shouldn't find it
    silently pre-populated."""
    from apps.accounts.models import Role, User, assign_role

    role = Role.objects.create(hospital=hospital, department=department, name="Custom Role")
    assert role.group.permissions.count() == 0

    from rest_framework.test import APIClient
    user = User.objects.create_user(email="custom@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(user, role)
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post("/api/v1/enquiries/", {"name": "X", "mobile": "9000000005", "source": "ivr"}, format="json")
    assert response.status_code == 403


@pytest.mark.django_db
def test_custom_actions_are_not_gated_by_the_model_permission_check(restricted_client, hospital, department):
    """check-in/claim/etc. are deliberately out of scope for
    RoleBasedModelPermissions (view.action isn't one of
    create/update/partial_update/destroy for a custom @action) — a
    Telephony Operator can claim a callback task even without an explicit
    "change_callbacktask" grant, since claim/complete/log_attempt already
    exist specifically as this role's day-to-day workflow."""
    from apps.telephony.models import CallbackTask
    from django.utils import timezone
    import datetime

    task = CallbackTask.objects.create(hospital=hospital, phone_number="9000000006", sla_due_at=timezone.now() + datetime.timedelta(minutes=15))

    response = restricted_client.post(f"/api/v1/callback-tasks/{task.id}/claim/")
    assert response.status_code == 200


# --- Staff X-Hospital-Id cross-hospital override --------------------------

@pytest.mark.django_db
def test_staff_with_x_hospital_id_header_sees_that_hospitals_data(api_client, staff_user, other_hospital, other_department):
    from apps.appointments.models import Doctor
    theirs = Doctor.objects.create(hospital=other_hospital, department=other_department, name="Not staff's own hospital")
    api_client.force_authenticate(user=staff_user)

    response = api_client.get("/api/v1/doctors/", HTTP_X_HOSPITAL_ID=str(other_hospital.id))

    ids = {row["id"] for row in response.data["results"]}
    assert theirs.id in ids


@pytest.mark.django_db
def test_staff_without_the_header_still_sees_only_their_own_hospital(api_client, staff_user, hospital, other_hospital, department, other_department):
    from apps.appointments.models import Doctor
    mine = Doctor.objects.create(hospital=hospital, department=department, name="Mine")
    Doctor.objects.create(hospital=other_hospital, department=other_department, name="Not mine")
    api_client.force_authenticate(user=staff_user)

    response = api_client.get("/api/v1/doctors/")

    ids = {row["id"] for row in response.data["results"]}
    assert ids == {mine.id}


@pytest.mark.django_db
def test_non_staff_x_hospital_id_header_is_ignored(auth_client, other_hospital, other_department):
    """Non-staff can't use the header to escalate — same as switch-hospital
    being staff-only."""
    from apps.appointments.models import Doctor
    theirs = Doctor.objects.create(hospital=other_hospital, department=other_department, name="Not mine")

    response = auth_client.get("/api/v1/doctors/", HTTP_X_HOSPITAL_ID=str(other_hospital.id))

    ids = {row["id"] for row in response.data["results"]}
    assert theirs.id not in ids


# --- AuditLog: DB-level immutability ---------------------------------------

@pytest.mark.django_db
def test_auditlog_save_guard_blocks_a_second_save_on_the_same_instance(hospital):
    from apps.core.models import AuditLog
    log = AuditLog.objects.create(hospital=hospital, action=AuditLog.Action.REQUEST, method="GET", path="/x/")
    log.path = "/changed/"
    with pytest.raises(ValueError):
        log.save()


@pytest.mark.django_db
def test_auditlog_instance_delete_guard_raises(hospital):
    from apps.core.models import AuditLog
    log = AuditLog.objects.create(hospital=hospital, action=AuditLog.Action.REQUEST, method="GET", path="/x/")
    with pytest.raises(ValueError):
        log.delete()


@pytest.mark.django_db
def test_auditlog_queryset_update_bypasses_the_python_guard_but_db_trigger_still_blocks_it(hospital):
    """The real gap this migration closes: QuerySet.update() never calls
    Model.save() on each row — it issues one UPDATE statement directly —
    so AuditLog.save()'s `if self.pk is not None: raise` guard is never
    reached. Before the DB trigger, this silently succeeded."""
    from django.db import IntegrityError, transaction
    from apps.core.models import AuditLog

    log = AuditLog.objects.create(hospital=hospital, action=AuditLog.Action.REQUEST, method="GET", path="/x/")

    with pytest.raises(IntegrityError), transaction.atomic():
        AuditLog.objects.filter(pk=log.pk).update(path="/tampered/")

    log.refresh_from_db()
    assert log.path == "/x/"


@pytest.mark.django_db
def test_auditlog_raw_sql_delete_is_blocked_at_the_database_level(hospital):
    """Same gap, worse case: a raw SQL DELETE (compromised credential,
    dbshell, a future bypass of the ORM entirely) never touches Python at
    all. Only a database-engine-level constraint can stop this."""
    from django.db import IntegrityError, connection, transaction
    from apps.core.models import AuditLog

    log = AuditLog.objects.create(hospital=hospital, action=AuditLog.Action.REQUEST, method="GET", path="/x/")

    with pytest.raises(IntegrityError), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM core_auditlog WHERE id = %s", [log.pk])

    assert AuditLog.objects.filter(pk=log.pk).exists()
