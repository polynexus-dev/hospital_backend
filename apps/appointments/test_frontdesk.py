"""OPD front desk: registration, visit and billing in one step.
NABH AAC.1.a/c/e/f (registration, UHID, ABHA, duplicates), AAC.2.a (visit),
FPM.3.d (bills), FPM.3.b (discounts with approval)."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role, User, assign_role
from apps.appointments.models import Doctor, OPDVisit
from apps.billing.models import Bill, DiscountApproval, PatientAdvance
from apps.core.models import PinCode
from apps.finance.models import ConsultationFeeRule, FrontDeskPolicy, ServiceTariff
from apps.patients.models import Patient

URL = "/api/v1/frontdesk/registrations/"


@pytest.fixture
def setup(hospital, department):
    doctor = Doctor.objects.create(hospital=hospital, department=department, name="Mehta", default_consultation_minutes=10)
    new = ServiceTariff.objects.create(hospital=hospital, code="CONS-NEW", name="Consultation", rate=Decimal("500"), category_rates={"private": 800})
    follow = ServiceTariff.objects.create(hospital=hospital, code="CONS-FU", name="Follow-up", rate=Decimal("300"))
    reg = ServiceTariff.objects.create(hospital=hospital, code="REG", name="Registration fee", rate=Decimal("100"))
    ConsultationFeeRule.objects.create(hospital=hospital, doctor=doctor, new_visit_tariff=new, follow_up_tariff=follow,
                                       free_follow_up_days=7, max_free_follow_ups=1, follow_up_valid_days=30)
    policy = FrontDeskPolicy.for_hospital(hospital.pk)
    policy.registration_fee_tariff = reg
    policy.save()
    return {"doctor": doctor, "policy": policy}


def body(doctor, **over):
    data = {
        "patient": {"title": "Mrs", "first_name": "Sunita", "last_name": "Patil", "gender": "female", "age_years": 45, "mobile": "9876501234",
                    "state": "Maharashtra", "district": "Pune", "pincode": "411038"},
        "visit": {"doctor": doctor.pk, "payer_type": "self"},
        "billing": {"settlement": "receipt", "payments": [{"method": "cash", "amount": 400}, {"method": "upi", "amount": 200, "reference": "UPI123"}]},
    }
    for key, value in over.items():
        data[key] = {**data.get(key, {}), **value} if isinstance(value, dict) else value
    return data


def front_desk_client(hospital, department):
    clerk = User.objects.create_user(email="counter@test-hospital.example", password="x", hospital=hospital, department=department)
    assign_role(clerk, Role.objects.create(hospital=hospital, department=department, name="Counter", template=Role.Template.FRONT_DESK))
    client = APIClient()
    client.force_authenticate(user=clerk)
    return client, clerk


@pytest.mark.django_db
def test_new_patient_registered_billed_and_paid_in_one_step(auth_client, setup):
    res = auth_client.post(URL, body(setup["doctor"], abha_number="91-1234-5678-9012"), format="json")
    assert res.status_code == 201, res.data
    patient = Patient.objects.get(pk=res.data["patient"]["id"])
    assert patient.uhid and patient.dob_is_estimated and timezone.localdate().year - patient.date_of_birth.year in (44, 45)
    assert patient.abha_link.abha_number == "91-1234-5678-9012"
    visit = OPDVisit.objects.get(pk=res.data["visit"]["id"])
    assert visit.visit_type == "new" and visit.is_new_patient and visit.appointment.queue_token == 1 and visit.appointment.status == "checked_in"
    bill = visit.bill
    assert [i.description for i in bill.items.all()] == ["Registration fee", "Consultation - Dr. Mehta"]
    assert bill.net_amount == Decimal("600") and bill.status == Bill.Status.PAID and bill.payments.count() == 2
    for link in res.data["links"].values():
        assert auth_client.get(link).content[:4] == b"%PDF"


@pytest.mark.django_db
def test_follow_up_free_then_paid_then_new(auth_client, setup):
    first = auth_client.post(URL, body(setup["doctor"]), format="json").data
    patient_id, doctor = first["patient"]["id"], setup["doctor"]
    again = {"patient_id": patient_id, "visit": {"doctor": doctor.pk}, "billing": {"settlement": "receipt", "payments": []}}

    free = auth_client.post(URL, again, format="json")
    assert free.status_code == 201, free.data
    assert free.data["visit"]["visit_type"] == "free_follow_up" and free.data["bill"]["net"] == Decimal("0")

    paid = auth_client.post(URL, {**again, "billing": {"payments": [{"method": "cash", "amount": 300}]}}, format="json")
    assert paid.data["visit"]["visit_type"] == "follow_up" and paid.data["bill"]["net"] == Decimal("300")

    OPDVisit.objects.filter(patient_id=patient_id).update(created_at=timezone.now() - timedelta(days=40))
    preview = auth_client.get("/api/v1/frontdesk/preview/", {"doctor": doctor.pk, "patient": patient_id}).data
    assert preview["visit_type"] == "new" and preview["total"] == Decimal("500")  # no registration fee for a returning patient


@pytest.mark.django_db
def test_rate_category_and_rounding(auth_client, setup):
    data = body(setup["doctor"], visit={"rate_category": "private"}, billing={"discount_amount": "45.50", "discount_reason": "Staff family", "payments": [{"method": "cash", "amount": 854}]})
    res = auth_client.post(URL, data, format="json")
    assert res.status_code == 201, res.data
    assert res.data["bill"]["total"] == Decimal("900") and res.data["bill"]["round_off"] == Decimal("0.50") and res.data["bill"]["net"] == Decimal("854")


@pytest.mark.django_db
def test_discount_above_limit_needs_an_approver(setup, hospital, department, auth_client, user):
    clerk, _ = front_desk_client(hospital, department)
    big = {"discount_percent": 50, "discount_reason": "Hardship", "payments": [{"method": "cash", "amount": 300}]}
    refused = clerk.post(URL, body(setup["doctor"], billing=big), format="json")
    assert refused.status_code == 400 and "after the discount is approved" in str(refused.data)
    assert not Patient.objects.exists()  # nothing half-saved

    pending = clerk.post(URL, body(setup["doctor"], billing={**big, "payments": []}), format="json")
    assert pending.status_code == 201 and pending.data["awaiting_discount_approval"]
    assert pending.data["bill"]["status"] == Bill.Status.DRAFT
    approval_id = pending.data["awaiting_discount_approval"]
    assert clerk.post(f"/api/v1/frontdesk/discount-approvals/{approval_id}/approve/").status_code == 403
    assert auth_client.post(f"/api/v1/frontdesk/discount-approvals/{approval_id}/approve/").data["status"] == "approved"
    assert Bill.objects.get(bill_number=pending.data["bill"]["bill_number"]).status == Bill.Status.UNPAID

    wrong = clerk.post(URL, body(setup["doctor"], patient={"mobile": "9876509999", "first_name": "Ravi"}, billing={**big, "approver": user.pk, "approver_password": "nope"}), format="json")
    assert wrong.status_code == 400
    ok = clerk.post(URL, body(setup["doctor"], patient={"mobile": "9876509999", "first_name": "Ravi"}, billing={**big, "approver": user.pk, "approver_password": "testpass123"}), format="json")
    assert ok.status_code == 201, ok.data
    assert DiscountApproval.objects.get(bill__bill_number=ok.data["bill"]["bill_number"]).approved_at_counter
    assert ok.data["bill"]["status"] == Bill.Status.PAID


@pytest.mark.django_db
def test_credit_only_for_allowed_payers(auth_client, setup):
    self_pay = auth_client.post(URL, body(setup["doctor"], billing={"settlement": "credit", "payments": []}), format="json")
    assert self_pay.status_code == 400 and "settlement" in self_pay.data
    corporate = auth_client.post(URL, body(setup["doctor"], visit={"payer_type": "corporate"}, billing={"settlement": "credit", "payments": []}), format="json")
    assert corporate.status_code == 201 and corporate.data["bill"]["status"] == Bill.Status.UNPAID


@pytest.mark.django_db
def test_advance_deposit_used_against_a_bill(auth_client, setup):
    patient_id = auth_client.post(URL, body(setup["doctor"]), format="json").data["patient"]["id"]
    deposit = auth_client.post("/api/v1/frontdesk/advances/", {"patient": patient_id, "amount": 1000, "payment_method": "upi"}, format="json")
    assert deposit.status_code == 201 and deposit.data["receipt_number"].startswith("ADV")
    OPDVisit.objects.filter(patient_id=patient_id).update(created_at=timezone.now() - timedelta(days=40))
    too_much = {"patient_id": patient_id, "visit": {"doctor": setup["doctor"].pk}, "billing": {"payments": [{"method": "advance", "amount": 2000}]}}
    assert auth_client.post(URL, too_much, format="json").status_code == 400
    res = auth_client.post(URL, {**too_much, "billing": {"payments": [{"method": "advance", "amount": 500}]}}, format="json")
    assert res.status_code == 201, res.data
    assert PatientAdvance.balance(Patient.objects.get(pk=patient_id)) == Decimal("500")
    ledger = auth_client.get("/api/v1/frontdesk/advances/", {"patient": patient_id}).data
    assert ledger["balance"] == Decimal("500") and {e["kind"] for e in ledger["entries"]} == {"deposit", "adjustment"}


@pytest.mark.django_db
def test_duplicates_and_wrong_totals_save_nothing(auth_client, setup):
    assert auth_client.post(URL, body(setup["doctor"], patient={"date_of_birth": "1980-01-01", "age_years": None}), format="json").status_code == 201
    dup = auth_client.post(URL, body(setup["doctor"], patient={"date_of_birth": "1980-01-01", "age_years": None}), format="json")
    assert dup.status_code == 409 and dup.data["matches"][0]["match"] == "exact"
    short = auth_client.post(URL, body(setup["doctor"], patient={"mobile": "9876500000"}, billing={"payments": [{"method": "cash", "amount": 100}]}), format="json")
    assert short.status_code == 400 and "payments" in short.data
    assert Patient.objects.count() == 1 and OPDVisit.objects.count() == 1


@pytest.mark.django_db
def test_consent_captured_at_registration(auth_client, setup):
    notice = auth_client.post("/api/v1/privacy/consent-notices/", {"purpose": "treatment", "title": "Treatment", "body": "We use your data to treat you."}, format="json").data
    res = auth_client.post(URL, body(setup["doctor"], consents=[{"notice": notice["id"], "status": "granted", "method": "written"}]), format="json")
    assert res.status_code == 201, res.data
    rows = {r["purpose"]: r for r in auth_client.get(f"/api/v1/privacy/consents/summary/?patient={res.data['patient']['id']}").data}
    assert rows["treatment"]["consent"]["status"] == "granted"


@pytest.mark.django_db
def test_pincode_lookup_and_states(auth_client):
    PinCode.objects.create(pincode="411038", office_name="Kothrud", sub_district="Haveli", district="Pune", state="Maharashtra")
    PinCode.objects.create(pincode="411038", office_name="Karvenagar", sub_district="Haveli", district="Pune", state="Maharashtra")
    res = auth_client.get("/api/v1/locations/pincode/411038/").data
    assert res["found"] and res["district"] == "Pune" and res["areas"] == ["Karvenagar", "Kothrud"]
    assert auth_client.get("/api/v1/locations/pincode/999999/").data["found"] is False
    states = auth_client.get("/api/v1/locations/states/").data
    assert len(states) == 36 and {"name": "Ladakh", "is_union_territory": True} in states


@pytest.mark.django_db
def test_roles_without_registration_rights_are_refused(setup, hospital, department):
    hr = User.objects.create_user(email="hr@test-hospital.example", password="x", hospital=hospital, department=department)
    assign_role(hr, Role.objects.create(hospital=hospital, department=department, name="HR", template=Role.Template.HR_MANAGER))
    client = APIClient()
    client.force_authenticate(user=hr)
    assert client.post(URL, body(setup["doctor"]), format="json").status_code == 403


@pytest.mark.django_db
def test_register_only_without_a_visit(auth_client, setup, hospital):
    """+ New Patient on the Patients page: registration without a visit,
    charging the registration fee if the hospital has one."""
    preview = auth_client.get("/api/v1/frontdesk/preview/", {"register_only": 1}).data
    assert [c["description"] for c in preview["charges"]] == ["Registration fee"] and preview["total"] == Decimal("100")

    data = body(setup["doctor"], register_only=True, billing={"payments": [{"method": "cash", "amount": 100}]})
    data.pop("visit")
    res = auth_client.post(URL, data, format="json")
    assert res.status_code == 201, res.data
    assert res.data["visit"] is None and res.data["bill"]["net"] == Decimal("100") and res.data["bill"]["status"] == "paid"
    assert not OPDVisit.objects.exists()
    assert auth_client.get(res.data["links"]["receipt"]).content[:4] == b"%PDF"
    assert auth_client.get(res.data["links"]["card"]).content[:4] == b"%PDF"

    # No registration fee configured: nothing to bill.
    FrontDeskPolicy.objects.filter(hospital=hospital).update(registration_fee_tariff=None)
    free = auth_client.post(URL, {**data, "patient": {**data["patient"], "mobile": "9876500077", "first_name": "Kiran"}, "billing": {}}, format="json")
    assert free.status_code == 201 and free.data["bill"] is None and free.data["links"]["receipt"] is None

    existing = auth_client.post(URL, {"register_only": True, "patient_id": res.data["patient"]["id"]}, format="json")
    assert existing.status_code == 400
