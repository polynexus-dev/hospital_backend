import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.enquiries.models import Enquiry, EnquiryAssignmentChange, EnquiryStageChange
from apps.enquiries.scoring import (
    MIN_TRAINING_SAMPLES,
    _heuristic_score,
    _train_model,
    extract_features,
    recompute_hospital_scores,
    score_enquiry,
)
from apps.enquiries.services import assign_enquiry, find_duplicates, move_stage


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

    response = auth_client.post(
        f"/api/v1/enquiries/{enquiry.id}/lose/",
        {"lost_reason": Enquiry.LostReason.WENT_ELSEWHERE, "lost_notes": "Chose another hospital"},
        format="json",
    )

    assert response.status_code == 200
    enquiry.refresh_from_db()
    assert enquiry.stage == Enquiry.Stage.LOST
    assert enquiry.lost_reason == Enquiry.LostReason.WENT_ELSEWHERE
    assert enquiry.lost_notes == "Chose another hospital"


@pytest.mark.django_db
def test_lose_action_requires_lost_reason(auth_client, hospital):
    enquiry = Enquiry.objects.create(hospital=hospital, name="X", mobile="9000099999", source=Enquiry.Source.OTHER)
    response = auth_client.post(f"/api/v1/enquiries/{enquiry.id}/lose/", {}, format="json")
    assert response.status_code == 400


# --- reassign: manual ownership change is logged --------------------------

@pytest.mark.django_db
def test_reassign_action_changes_owner_and_logs_assignment_change(auth_client, hospital, department, user):
    from apps.accounts.models import Role, User, assign_role

    other_role = Role.objects.create(hospital=hospital, department=department, name="Other Front Desk", template=Role.Template.ADMIN)
    other_owner = User.objects.create_user(email="other-owner@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(other_owner, other_role)

    enquiry = Enquiry.objects.create(hospital=hospital, name="X", mobile="9000010001", source=Enquiry.Source.OTHER, assigned_to=user)

    response = auth_client.post(f"/api/v1/enquiries/{enquiry.id}/reassign/", {"owner": other_owner.id, "reason": "on leave"}, format="json")

    assert response.status_code == 200
    enquiry.refresh_from_db()
    assert enquiry.assigned_to_id == other_owner.id
    change = EnquiryAssignmentChange.objects.get(enquiry=enquiry)
    assert change.from_owner_id == user.id
    assert change.to_owner_id == other_owner.id
    assert change.reason == "on leave"


@pytest.mark.django_db
def test_reassign_action_rejects_owner_from_another_hospital(auth_client, hospital, other_user):
    enquiry = Enquiry.objects.create(hospital=hospital, name="X", mobile="9000010002", source=Enquiry.Source.OTHER)
    response = auth_client.post(f"/api/v1/enquiries/{enquiry.id}/reassign/", {"owner": other_user.id}, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_assign_enquiry_logs_an_assignment_change(hospital, department, user):
    enquiry = Enquiry.objects.create(hospital=hospital, name="Ravi Kumar", mobile="9123456781", source=Enquiry.Source.WALK_IN, department=department)
    assign_enquiry(enquiry)
    change = EnquiryAssignmentChange.objects.get(enquiry=enquiry)
    assert change.from_owner_id is None
    assert change.to_owner_id == user.id


# --- merge: duplicate consolidation ----------------------------------------

@pytest.mark.django_db
def test_merge_action_consolidates_duplicate_into_primary(auth_client, hospital):
    primary = Enquiry.objects.create(hospital=hospital, name="Asha Patil", mobile="9876500001", source=Enquiry.Source.WEBSITE, notes="Primary notes")
    duplicate = Enquiry.objects.create(hospital=hospital, name="Asha P.", mobile="9111100002", source=Enquiry.Source.IVR, notes="Called about OPD")

    response = auth_client.post(f"/api/v1/enquiries/{duplicate.id}/merge/", {"primary_id": primary.id}, format="json")

    assert response.status_code == 200
    duplicate.refresh_from_db()
    assert duplicate.duplicate_of_id == primary.id
    assert duplicate.stage == Enquiry.Stage.LOST
    assert duplicate.lost_reason == Enquiry.LostReason.DUPLICATE
    primary.refresh_from_db()
    assert "Called about OPD" in primary.notes


@pytest.mark.django_db
def test_merge_action_rejects_cross_hospital_merge(auth_client, hospital, other_hospital):
    primary = Enquiry.objects.create(hospital=other_hospital, name="Elsewhere", mobile="9000010003", source=Enquiry.Source.WEBSITE)
    duplicate = Enquiry.objects.create(hospital=hospital, name="X", mobile="9000010004", source=Enquiry.Source.OTHER)

    response = auth_client.post(f"/api/v1/enquiries/{duplicate.id}/merge/", {"primary_id": primary.id}, format="json")
    assert response.status_code == 400


# --- lead-capture webhook ----------------------------------------------------

@pytest.mark.django_db
def test_lead_webhook_creates_enquiry_for_valid_token(api_client, hospital):
    payload = {
        "name": "Website Lead", "mobile": "9000020001", "source": "website",
        "utm_source": "google", "utm_medium": "cpc", "utm_campaign": "cardiology-launch",
    }
    response = api_client.post(f"/api/v1/enquiries/lead-webhook/{hospital.lead_webhook_token}/", payload, format="json")

    assert response.status_code == 201
    enquiry = Enquiry.objects.get(pk=response.data["id"])
    assert enquiry.hospital_id == hospital.id
    assert enquiry.utm_source == "google"
    assert enquiry.utm_campaign == "cardiology-launch"


@pytest.mark.django_db
def test_lead_webhook_rejects_unknown_token(api_client):
    response = api_client.post(
        "/api/v1/enquiries/lead-webhook/00000000-0000-0000-0000-000000000000/",
        {"name": "X", "mobile": "9000020002"}, format="json",
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_lead_webhook_requires_name_and_mobile(api_client, hospital):
    response = api_client.post(f"/api/v1/enquiries/lead-webhook/{hospital.lead_webhook_token}/", {"name": "X"}, format="json")
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


@pytest.mark.django_db
def test_bulk_import_rejects_a_file_over_the_row_cap(auth_client, hospital):
    """Per-tenant resource isolation: bulk_import does one DB insert per row
    synchronously in a single request — MAX_BULK_IMPORT_ROWS bounds how long
    one hospital's import can tie up a worker/DB connection that every other
    hospital's requests also depend on (see EnquiryViewSet.MAX_BULK_IMPORT_ROWS)."""
    from apps.enquiries.views import EnquiryViewSet

    header = "name,mobile,email,source,service_requested\r\n"
    rows = "".join(f"Lead {i},9{i:09d},,walk_in,OPD\r\n" for i in range(EnquiryViewSet.MAX_BULK_IMPORT_ROWS + 1))
    upload = SimpleUploadedFile("leads.csv", (header + rows).encode("utf-8"), content_type="text/csv")

    response = auth_client.post("/api/v1/enquiries/bulk-import/", {"file": upload}, format="multipart")

    assert response.status_code == 400
    assert Enquiry.objects.filter(hospital=hospital).count() == 0


def test_bulk_import_is_scoped_to_the_heavy_ops_throttle():
    """Only the bulk_import action should carry the heavy_ops scope — proves
    get_throttles() doesn't leak it onto the rest of this viewset's actions.
    ScopedRateThrottle resolves its .scope from view.throttle_scope lazily
    (inside allow_request), so the real assertion is on that attribute, not
    on the freshly-constructed throttle instance itself."""
    from rest_framework.throttling import ScopedRateThrottle

    from apps.enquiries.views import EnquiryViewSet

    view = EnquiryViewSet()
    view.action = "bulk_import"
    throttles = view.get_throttles()
    assert len(throttles) == 1
    assert isinstance(throttles[0], ScopedRateThrottle)
    assert view.throttle_scope == "heavy_ops"

    other_view = EnquiryViewSet()
    other_view.action = "list"
    other_view.get_throttles()
    assert not hasattr(other_view, "throttle_scope")


@pytest.mark.django_db
def test_heavy_ops_endpoints_are_rate_limited_to_20_per_hour(api_client, user):
    """config.settings.test disables DEFAULT_THROTTLE_CLASSES suite-wide
    (see that file's docstring) — re-enable it just for this one test to
    prove the per-tenant "heavy_ops" ceiling (DEFAULT_THROTTLE_RATES in
    settings) actually works, same technique as apps.accounts.tests.
    test_login_is_rate_limited_to_5_per_minute_per_ip. DataExportView is the
    cheapest heavy_ops-scoped view to exercise repeatedly (an empty CSV of
    zero patients); FHIRExportView/MISExportView/EnquiryViewSet.bulk_import
    share the exact same scope+rate, not a separately-configured one."""
    from django.core.cache import cache
    from rest_framework.throttling import ScopedRateThrottle

    from apps.integrations.views import DataExportView

    cache.clear()
    api_client.force_authenticate(user=user)

    original_throttle_classes = DataExportView.throttle_classes
    DataExportView.throttle_classes = [ScopedRateThrottle]
    try:
        for _ in range(20):
            response = api_client.get("/api/v1/export/patients/")
            assert response.status_code == 200

        blocked = api_client.get("/api/v1/export/patients/")
        assert blocked.status_code == 429
    finally:
        DataExportView.throttle_classes = original_throttle_classes
        cache.clear()


# --- webhook-config & TreatmentEstimate tests --------------------------------

@pytest.mark.django_db
def test_webhook_config_returns_hospital_token_and_payload(auth_client, hospital):
    response = auth_client.get("/api/v1/enquiries/webhook-config/")
    assert response.status_code == 200
    assert response.data["token"] == str(hospital.lead_webhook_token)
    assert str(hospital.lead_webhook_token) in response.data["webhook_url"]
    assert "sample_payload" in response.data


@pytest.mark.django_db
def test_treatment_estimate_lifecycle_and_pdf(auth_client, hospital):
    from apps.patients.models import Patient
    patient = Patient.objects.create(hospital=hospital, first_name="Asha", last_name="Patil", mobile="9800000000")
    payload = {
        "patient": patient.id,
        "procedure_name": "Total Knee Replacement",
        "diagnosis": "Severe Osteoarthritis Grade IV",
        "room_category": "private",
        "stay_days": 3,
        "surgeon_fee": "50000.00",
        "ot_charges": "30000.00",
        "room_charges": "15000.00",
        "medicines_estimate": "25000.00",
        "implants_investigations": "60000.00",
        "payment_mode": "insurance",
        "tpa_name": "Star Health",
        "insurance_preauth_status": "approved",
        "approved_preauth_amount": "170000.00",
    }
    create_res = auth_client.post("/api/v1/treatment-estimates/", payload, format="json")
    assert create_res.status_code == 201
    estimate_id = create_res.data["id"]
    assert float(create_res.data["total_estimate"]) == 180000.00

    # Test PDF download
    pdf_res = auth_client.get(f"/api/v1/treatment-estimates/{estimate_id}/pdf/")
    assert pdf_res.status_code == 200
    assert pdf_res["Content-Type"] == "application/pdf"
    assert len(pdf_res.content) > 1000

    # Test convert to admission
    convert_res = auth_client.post(f"/api/v1/treatment-estimates/{estimate_id}/convert-admission/")
    assert convert_res.status_code == 200
    assert convert_res.data["stage"] == "converted"


@pytest.mark.django_db
def test_export_csv_and_history_and_notes(auth_client, hospital):
    import datetime
    today = datetime.date.today()
    enquiry = Enquiry.objects.create(
        hospital=hospital,
        name="Sunil Gavaskar",
        mobile="9811223344",
        source=Enquiry.Source.WEBSITE,
        follow_up_date=today,
        service_requested="Orthopedics Consultation",
    )

    # Test export CSV
    res_csv = auth_client.get("/api/v1/enquiries/export-csv/")
    assert res_csv.status_code == 200
    assert "text/csv" in res_csv["Content-Type"]
    content = res_csv.content.decode("utf-8-sig")
    assert "Sunil Gavaskar" in content
    assert "9811223344" in content
    assert "Orthopedics Consultation" in content

    # Test add note
    note_res = auth_client.post(f"/api/v1/enquiries/{enquiry.id}/add-note/", {"note": "Called patient, requested Sunday."}, format="json")
    assert note_res.status_code == 200
    assert "Called patient, requested Sunday" in note_res.data["notes"]

    # Test move stage and verify history
    auth_client.post(f"/api/v1/enquiries/{enquiry.id}/move-stage/", {"stage": "contacted"}, format="json")
    history_res = auth_client.get(f"/api/v1/enquiries/{enquiry.id}/history/")
    assert history_res.status_code == 200
    assert len(history_res.data["stage_changes"]) >= 1
    assert history_res.data["stage_changes"][0]["to_stage"] == "contacted"


# --- Lead scoring (apps.enquiries.scoring) ----------------------------------
#
# Two tiers, tested separately and deliberately: the heuristic (always
# available, day-one) and the trained model (only once a hospital has real
# closed-enquiry history — see MIN_TRAINING_SAMPLES). A test that only
# compares final scores can't tell which tier actually produced them, since
# the heuristic and a well-trained model would often agree on direction —
# so the trained-model tests below call `_train_model`/`score_enquiry`
# directly with an explicit `model=`, not through the day-one heuristic path.

def _make_closed_enquiry(hospital, *, mobile, good: bool):
    """`good=True` -> urgent/referral/has-estimate, moved to COMPLETED.
    `good=False` -> low-priority/other-source/no-estimate, moved to LOST.
    Deliberately leaves `department` unset on both — apps.enquiries.signals'
    auto-assignment would otherwise inject `assigned_to` inconsistently and
    muddy the two groups' features for no reason relevant to this test."""
    enquiry = Enquiry.objects.create(
        hospital=hospital,
        name="Synthetic Lead",
        mobile=mobile,
        source=Enquiry.Source.REFERRAL if good else Enquiry.Source.OTHER,
        urgency=Enquiry.Urgency.URGENT if good else Enquiry.Urgency.LOW,
        estimated_value=50000 if good else None,
    )
    move_stage(enquiry, Enquiry.Stage.COMPLETED if good else Enquiry.Stage.LOST)
    return enquiry


@pytest.mark.django_db
def test_extract_features_reads_the_documented_signals(hospital, department, user):
    enquiry = Enquiry.objects.create(
        hospital=hospital, name="Priya Deshmukh", mobile="9800011111",
        source=Enquiry.Source.WALK_IN, urgency=Enquiry.Urgency.HIGH,
        department=department, estimated_value=15000,
    )
    features = extract_features(enquiry)
    assert features["urgency"] == 2  # high
    assert features["has_department"] == 1
    assert features["has_estimated_value"] == 1
    assert features["source"] == Enquiry.Source.WALK_IN


@pytest.mark.django_db
def test_heuristic_score_rewards_urgent_referral_leads_over_low_priority_ones(hospital):
    strong = Enquiry.objects.create(
        hospital=hospital, name="A", mobile="9800022221", source=Enquiry.Source.REFERRAL,
        urgency=Enquiry.Urgency.URGENT, estimated_value=20000,
    )
    weak = Enquiry.objects.create(
        hospital=hospital, name="B", mobile="9800022222", source=Enquiry.Source.OTHER,
        urgency=Enquiry.Urgency.LOW,
    )
    assert _heuristic_score(strong) > _heuristic_score(weak)


@pytest.mark.django_db
def test_heuristic_score_is_capped_between_0_and_100(hospital, department, user):
    maxed = Enquiry.objects.create(
        hospital=hospital, name="Maxed", mobile="9800033331", source=Enquiry.Source.REFERRAL,
        urgency=Enquiry.Urgency.URGENT, department=department, estimated_value=999999,
    )
    assert 0 <= _heuristic_score(maxed) <= 100


@pytest.mark.django_db
def test_enquiry_creation_sets_a_score_automatically(hospital):
    """Pins the signals.py wiring — a plain create() through the ORM (not
    just the API) must end up with a real, non-zero score for an
    otherwise-qualified lead, with no caller having to ask for it."""
    enquiry = Enquiry.objects.create(
        hospital=hospital, name="Auto Scored", mobile="9800044441",
        source=Enquiry.Source.REFERRAL, urgency=Enquiry.Urgency.HIGH,
    )
    enquiry.refresh_from_db()
    assert enquiry.score > 0


@pytest.mark.django_db
def test_train_model_returns_none_below_the_minimum_sample_threshold(hospital):
    for i in range(10):
        _make_closed_enquiry(hospital, mobile=f"98001{i:05d}", good=(i % 2 == 0))
    assert _train_model(hospital) is None


@pytest.mark.django_db
def test_train_model_returns_none_for_a_single_class_training_set(hospital):
    """MIN_TRAINING_SAMPLES rows, but every single one converted — logistic
    regression has no boundary to learn from a training set with only one
    outcome, so this must degrade to the heuristic, not raise."""
    for i in range(MIN_TRAINING_SAMPLES + 5):
        _make_closed_enquiry(hospital, mobile=f"98002{i:05d}", good=True)
    assert _train_model(hospital) is None


@pytest.mark.django_db
def test_train_model_fits_a_classifier_that_ranks_a_good_lead_above_a_bad_one(hospital):
    for i in range(30):
        _make_closed_enquiry(hospital, mobile=f"98003{i:05d}", good=True)
    for i in range(30):
        _make_closed_enquiry(hospital, mobile=f"98004{i:05d}", good=False)

    model = _train_model(hospital)
    assert model is not None

    good_open = Enquiry.objects.create(
        hospital=hospital, name="Good Open", mobile="9800055551",
        source=Enquiry.Source.REFERRAL, urgency=Enquiry.Urgency.URGENT, estimated_value=50000,
    )
    bad_open = Enquiry.objects.create(
        hospital=hospital, name="Bad Open", mobile="9800055552",
        source=Enquiry.Source.OTHER, urgency=Enquiry.Urgency.LOW,
    )
    assert score_enquiry(good_open, model=model) > score_enquiry(bad_open, model=model)


@pytest.mark.django_db
def test_training_never_pools_another_hospitals_enquiries(hospital, other_hospital):
    """The isolation proof: hospital's own history is too thin to train on
    by itself, and stays that way even though other_hospital has plenty of
    well-separated closed enquiries sitting in the same table."""
    for i in range(30):
        _make_closed_enquiry(other_hospital, mobile=f"98005{i:05d}", good=True)
    for i in range(30):
        _make_closed_enquiry(other_hospital, mobile=f"98006{i:05d}", good=False)
    for i in range(5):
        _make_closed_enquiry(hospital, mobile=f"98007{i:05d}", good=(i % 2 == 0))

    assert _train_model(other_hospital) is not None
    assert _train_model(hospital) is None


@pytest.mark.django_db
def test_recompute_hospital_scores_only_touches_open_stage_enquiries(hospital):
    closed = _make_closed_enquiry(hospital, mobile="9800066661", good=True)
    closed_score_before = closed.score

    recompute_hospital_scores(hospital)

    closed.refresh_from_db()
    assert closed.score == closed_score_before


@pytest.mark.django_db
def test_recompute_hospital_scores_updates_open_enquiries_and_returns_a_change_count(hospital):
    enquiry = Enquiry.objects.create(
        hospital=hospital, name="Needs Rescoring", mobile="9800077771",
        source=Enquiry.Source.REFERRAL, urgency=Enquiry.Urgency.URGENT,
    )
    # Force a score that's obviously wrong for this enquiry's own features,
    # so recompute has something real to correct.
    Enquiry.objects.filter(pk=enquiry.pk).update(score=0)

    updated_count = recompute_hospital_scores(hospital)

    enquiry.refresh_from_db()
    assert updated_count >= 1
    assert enquiry.score > 0


@pytest.mark.django_db
def test_recompute_enquiry_scores_task_skips_inactive_hospitals(hospital):
    from apps.enquiries.tasks import recompute_enquiry_scores

    hospital.is_active = False
    hospital.save(update_fields=["is_active"])

    enquiry = Enquiry.objects.create(hospital=hospital, name="Inactive Hosp", mobile="9800088881", source=Enquiry.Source.REFERRAL)
    Enquiry.objects.filter(pk=enquiry.pk).update(score=0)

    recompute_enquiry_scores()

    enquiry.refresh_from_db()
    assert enquiry.score == 0  # untouched — the hospital is inactive, so the task never reaches it


