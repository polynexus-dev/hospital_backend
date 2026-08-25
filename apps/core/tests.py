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


# --- HospitalGroup / Hospital.group (docs/erp/00-overview.md §2) ----------

@pytest.mark.django_db
def test_hospital_can_optionally_belong_to_a_hospital_group(hospital, other_hospital):
    from apps.core.models import HospitalGroup

    group = HospitalGroup.objects.create(name="Polynexus Network")
    hospital.group = group
    hospital.save(update_fields=["group"])

    assert group.hospitals.count() == 1
    assert other_hospital.group_id is None  # ungrouped hospitals are unaffected


@pytest.mark.django_db
def test_deleting_a_hospital_group_does_not_delete_its_hospitals(hospital):
    """SET_NULL, not CASCADE — HospitalGroup is reporting/ownership
    metadata only, never a tenant-isolation boundary (see
    docs/erp/00-overview.md §2). Deleting the group must never delete
    patient data."""
    from apps.core.models import HospitalGroup

    group = HospitalGroup.objects.create(name="Temp Group")
    hospital.group = group
    hospital.save(update_fields=["group"])
    hospital_id = hospital.id

    group.delete()

    from apps.core.models import Hospital
    remaining = Hospital.objects.get(pk=hospital_id)
    assert remaining.group_id is None


# --- Amendment (docs/erp/07-audit-and-security.md §2b) ---------------------

@pytest.mark.django_db
def test_amendment_can_reference_any_model_via_generic_relation(hospital, department):
    from django.contrib.contenttypes.models import ContentType

    from apps.core.models import Amendment

    amendment = Amendment.objects.create(
        hospital=hospital,
        content_type=ContentType.objects.get_for_model(department),
        object_id=str(department.pk),
        field_name="name",
        previous_value="OPD",
        corrected_value="OPD (Ground Floor)",
        reason="Corrected after physical department relocation.",
    )

    assert amendment.content_object == department
    assert str(department.pk) == amendment.object_id


# --- ActionPermissionRequired (docs/erp/03-rbac-and-roles.md §2a) ---------
#
# No real ViewSet declares action_permissions yet in Phase 2 (the first
# consumers — laboratory.verify_labresult etc. — ship with their models in
# later phases), so this tests the permission class's own logic in
# isolation against a minimal fake view, rather than through a live
# endpoint that doesn't exist yet.

class _FakeUser:
    def __init__(self, granted_perms):
        self._granted = set(granted_perms)

    def has_perm(self, perm):
        return perm in self._granted


class _FakeRequest:
    def __init__(self, user):
        self.user = user


class _FakeView:
    action = "verify"
    action_permissions = {"verify": "laboratory.verify_labresult"}


def test_action_permission_required_allows_a_user_with_the_named_permission():
    from apps.core.permissions import ActionPermissionRequired

    request = _FakeRequest(_FakeUser(["laboratory.verify_labresult"]))
    assert ActionPermissionRequired().has_permission(request, _FakeView()) is True


def test_action_permission_required_blocks_a_user_without_the_named_permission():
    from apps.core.permissions import ActionPermissionRequired

    request = _FakeRequest(_FakeUser([]))
    assert ActionPermissionRequired().has_permission(request, _FakeView()) is False


def test_action_permission_required_is_a_no_op_for_an_action_not_listed():
    from apps.core.permissions import ActionPermissionRequired

    class OtherActionView:
        action = "list"
        action_permissions = {"verify": "laboratory.verify_labresult"}

    request = _FakeRequest(_FakeUser([]))
    assert ActionPermissionRequired().has_permission(request, OtherActionView()) is True


def test_action_permission_required_is_a_no_op_for_a_view_declaring_no_action_permissions():
    from apps.core.permissions import ActionPermissionRequired

    class PlainViewSet:
        action = "create"

    request = _FakeRequest(_FakeUser([]))
    assert ActionPermissionRequired().has_permission(request, PlainViewSet()) is True


# --- TenantScopedViewSetMixin.assignment_scope_field (docs/erp/03-rbac-and-roles.md §2b) ---
#
# No real ViewSet sets assignment_scope_field yet in Phase 2 (the first
# consumers — nursing/ipd — ship in Phase 4). Proven here directly against
# the mixin, using patients.Document.uploaded_by (an existing FK to User)
# as a stand-in "assigned to" relation, rather than through a live
# endpoint that doesn't exist yet.

@pytest.mark.django_db
def test_assignment_scope_field_narrows_queryset_for_an_assigned_only_role(hospital, department, other_department):
    from contextlib import suppress
    from types import SimpleNamespace

    from apps.accounts.models import Role, User, assign_role
    from apps.core.viewsets import TenantScopedViewSetMixin
    from apps.patients.models import Document, Patient

    patient = Patient.objects.create(hospital=hospital, first_name="Scoped", mobile="9822000001")

    scoped_role = Role.objects.create(hospital=hospital, department=department, name="Scoped Role", data_scope=Role.DataScope.ASSIGNED_ONLY)
    nurse = User.objects.create_user(email="nurse@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(nurse, scoped_role)
    other_staff = User.objects.create_user(email="other-staff@test-hospital.example", password="testpass123", hospital=hospital, department=other_department)

    mine = Document.objects.create(hospital=hospital, patient=patient, uploaded_by=nurse)
    not_mine = Document.objects.create(hospital=hospital, patient=patient, uploaded_by=other_staff)

    class ScopedDocumentViewSet(TenantScopedViewSetMixin):
        queryset = Document.objects.all()
        assignment_scope_field = "uploaded_by"

    view = ScopedDocumentViewSet()
    view.request = SimpleNamespace(user=nurse, headers={})

    visible_ids = set(view.get_queryset().values_list("id", flat=True))
    assert visible_ids == {mine.id}
    assert not_mine.id not in visible_ids

    with suppress(Exception):
        mine.delete()
    with suppress(Exception):
        not_mine.delete()
    with suppress(Exception):
        patient.delete()


@pytest.mark.django_db
def test_assignment_scope_field_is_ignored_for_a_role_with_the_default_all_scope(hospital, department):
    from contextlib import suppress
    from types import SimpleNamespace

    from apps.accounts.models import Role, User, assign_role
    from apps.core.viewsets import TenantScopedViewSetMixin
    from apps.patients.models import Document, Patient

    patient = Patient.objects.create(hospital=hospital, first_name="Unscoped", mobile="9822000002")

    all_scope_role = Role.objects.create(hospital=hospital, department=department, name="All Scope Role")  # data_scope defaults to "all"
    doctor = User.objects.create_user(email="doctor@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(doctor, all_scope_role)
    someone_else = User.objects.create_user(email="someone-else@test-hospital.example", password="testpass123", hospital=hospital, department=department)

    mine = Document.objects.create(hospital=hospital, patient=patient, uploaded_by=doctor)
    also_visible = Document.objects.create(hospital=hospital, patient=patient, uploaded_by=someone_else)

    class ScopedDocumentViewSet(TenantScopedViewSetMixin):
        queryset = Document.objects.all()
        assignment_scope_field = "uploaded_by"

    view = ScopedDocumentViewSet()
    view.request = SimpleNamespace(user=doctor, headers={})

    visible_ids = set(view.get_queryset().values_list("id", flat=True))
    assert visible_ids == {mine.id, also_visible.id}

    with suppress(Exception):
        mine.delete()
    with suppress(Exception):
        also_visible.delete()
    with suppress(Exception):
        patient.delete()


# --- HospitalActive: suspended tenants are actually locked out ------------
#
# Hospital.is_active/HospitalViewSet.toggle_status existed before this
# fix, but nothing on the request path ever checked it —
# TenantScopedViewSetMixin/UserViewSet/RoleViewSet only ever filtered by
# hospital_id, never by hospital.is_active. These tests prove the new
# apps.core.permissions.HospitalActive global permission actually closes
# that gap.

@pytest.mark.django_db
def test_suspended_hospitals_user_is_locked_out_of_the_api(auth_client, hospital):
    hospital.is_active = False
    hospital.save(update_fields=["is_active"])

    response = auth_client.get("/api/v1/patients/")

    assert response.status_code == 403


@pytest.mark.django_db
def test_active_hospitals_user_is_unaffected(auth_client, hospital):
    assert hospital.is_active is True
    assert auth_client.get("/api/v1/patients/").status_code == 200


@pytest.mark.django_db
def test_staff_user_is_exempt_from_the_suspended_hospital_lockout(api_client, staff_user, hospital):
    """Staff/SaaS-admins need to keep working inside a suspended hospital
    (via X-Hospital-Id) to investigate or reactivate it."""
    hospital.is_active = False
    hospital.save(update_fields=["is_active"])
    api_client.force_authenticate(user=staff_user)

    response = api_client.get("/api/v1/patients/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_staff_only_user_with_no_hospital_is_unaffected(api_client):
    from apps.accounts.models import User

    staff_only = User.objects.create_user(email="ops-only@polynexus.in", password="x", is_staff=True)
    api_client.force_authenticate(user=staff_only)

    response = api_client.get("/api/v1/patients/")

    assert response.status_code == 200


# --- AuditLogViewSet: platform-wide trail for staff/SaaS-admins -----------

@pytest.mark.django_db
def test_staff_sees_audit_logs_across_every_hospital(api_client, staff_user, hospital, other_hospital):
    from apps.core.models import AuditLog

    AuditLog.objects.create(hospital=hospital, action=AuditLog.Action.REQUEST, method="GET", path="/mine/")
    AuditLog.objects.create(hospital=other_hospital, action=AuditLog.Action.REQUEST, method="GET", path="/not-mine/")
    api_client.force_authenticate(user=staff_user)

    response = api_client.get("/api/v1/audit-logs/")

    paths = {row["path"] for row in response.data["results"]}
    assert {"/mine/", "/not-mine/"} <= paths


@pytest.mark.django_db
def test_staff_can_narrow_audit_logs_to_one_hospital_via_query_param(api_client, staff_user, hospital, other_hospital):
    from apps.core.models import AuditLog

    AuditLog.objects.create(hospital=hospital, action=AuditLog.Action.REQUEST, method="GET", path="/mine/")
    AuditLog.objects.create(hospital=other_hospital, action=AuditLog.Action.REQUEST, method="GET", path="/not-mine/")
    api_client.force_authenticate(user=staff_user)

    response = api_client.get(f"/api/v1/audit-logs/?hospital={other_hospital.id}")

    paths = {row["path"] for row in response.data["results"]}
    assert paths == {"/not-mine/"}


@pytest.mark.django_db
def test_non_staff_user_still_only_sees_their_own_hospitals_audit_logs(user, other_hospital):
    """AuditLogViewSet is IsAdminUser-gated end-to-end (a non-staff user
    gets 403 before get_queryset() ever runs — see the next test), so this
    exercises get_queryset()'s non-staff branch directly rather than
    through the always-403 live endpoint, same style as this file's
    ActionPermissionRequired fake-request tests below."""
    from types import SimpleNamespace

    from apps.core.models import AuditLog
    from apps.core.views import AuditLogViewSet

    AuditLog.objects.create(hospital=user.hospital, action=AuditLog.Action.REQUEST, method="GET", path="/mine/")
    AuditLog.objects.create(hospital=other_hospital, action=AuditLog.Action.REQUEST, method="GET", path="/not-mine/")

    view = AuditLogViewSet()
    view.request = SimpleNamespace(user=user, query_params={})

    paths = {log.path for log in view.get_queryset()}
    assert paths == {"/mine/"}


@pytest.mark.django_db
def test_non_staff_user_gets_403_from_the_live_audit_log_endpoint(auth_client):
    """AuditLogViewSet stays IsAdminUser-gated end-to-end — the platform-
    wide broadening in this fix is for staff/SaaS-admins only, never a
    reason to open this endpoint to regular hospital users."""
    assert auth_client.get("/api/v1/audit-logs/").status_code == 403


# --- Hospital.slug validation (ValidationError import bug) ----------------

@pytest.mark.django_db
def test_reserved_hospital_slug_raises_validation_error_not_nameerror():
    from django.core.exceptions import ValidationError as DjangoValidationError

    from apps.core.models import validate_hospital_slug

    with pytest.raises(DjangoValidationError):
        validate_hospital_slug("admin")


# --- HospitalViewSet: module-key validation, suspend/onboard fixes --------

@pytest.mark.django_db
def test_update_modules_rejects_an_unknown_module_key(api_client, staff_user, hospital):
    api_client.force_authenticate(user=staff_user)
    response = api_client.post(f"/api/v1/hospitals/{hospital.id}/update-modules/", {"enabled_modules": ["pharmacy", "not-a-real-module"]}, format="json")
    assert response.status_code == 400
    hospital.refresh_from_db()
    assert "not-a-real-module" not in hospital.enabled_modules


@pytest.mark.django_db
def test_update_modules_accepts_known_module_keys(api_client, staff_user, hospital):
    api_client.force_authenticate(user=staff_user)
    response = api_client.post(f"/api/v1/hospitals/{hospital.id}/update-modules/", {"enabled_modules": ["pharmacy", "billing"]}, format="json")
    assert response.status_code == 200
    hospital.refresh_from_db()
    assert hospital.enabled_modules == ["pharmacy", "billing"]


@pytest.mark.django_db
def test_onboarding_does_not_grant_the_auto_provisioned_admin_is_staff(api_client, staff_user):
    """The bug this closes: every tenant's own auto-created "Hospital
    Owner / Admin" used to get is_staff=True, which — combined with
    Django admin's ModelAdmin classes having no per-hospital scoping at
    all — let every hospital's own admin read/write every *other*
    hospital's data via X-Hospital-Id, or browse it through /admin/."""
    from apps.accounts.models import User

    api_client.force_authenticate(user=staff_user)
    response = api_client.post("/api/v1/hospitals/", {"name": "New Onboarded Hospital", "slug": "new-onboarded"}, format="json")

    assert response.status_code == 201
    admin_email = f"admin@{response.data['slug']}.example"
    provisioned_admin = User.objects.get(email=admin_email)
    assert provisioned_admin.is_staff is False
    assert provisioned_admin.is_saas_admin is False


@pytest.mark.django_db
def test_onboarding_returns_a_generated_password_once(api_client, staff_user):
    api_client.force_authenticate(user=staff_user)
    response = api_client.post("/api/v1/hospitals/", {"name": "Another Onboarded Hospital", "slug": "another-onboarded"}, format="json")

    assert response.status_code == 201
    provisioned = response.data["provisioned_admin"]
    assert provisioned["email"] == f"admin@{response.data['slug']}.example"
    assert len(provisioned["password"]) >= 16

    from apps.accounts.models import User
    admin_user = User.objects.get(email=provisioned["email"])
    assert admin_user.check_password(provisioned["password"])


@pytest.mark.django_db
def test_onboarding_accepts_a_caller_supplied_admin_email(api_client, staff_user):
    api_client.force_authenticate(user=staff_user)
    response = api_client.post("/api/v1/hospitals/", {
        "name": "Custom Admin Hospital", "slug": "custom-admin-hospital", "admin_email": "owner@realclinic.example",
    }, format="json")

    assert response.status_code == 201
    assert response.data["provisioned_admin"]["email"] == "owner@realclinic.example"
