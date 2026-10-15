"""Insurance parity with MedNet M2.22: non-payable item lists with the
patient-payable split, the insurance inbox, and the NHCX pre-auth form."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.billing.models import Bill, BillItem
from apps.billing.services import recalculate_bill
from apps.finance.models import InsurancePolicy, ServiceTariff
from apps.patients.models import Patient
from apps.pharmacy.models import Medicine
from apps.tpa.models import Claim, ClaimMessage, NHCXProfile, NonPayableItem, NonPayableList, PreAuthRequest, TPACompany

TPA = "/api/v1/tpa"


@pytest.fixture
def s(hospital):
    star = TPACompany.objects.create(hospital=hospital, name="Star Health", code="STAR", avg_tat_days=2)
    other = TPACompany.objects.create(hospital=hospital, name="Care Insurance", code="CARE")
    patient = Patient.objects.create(hospital=hospital, first_name="Ravi", last_name="Kale", mobile="9811100501", gender="male")
    InsurancePolicy.objects.create(hospital=hospital, patient=patient, tpa_company=star, insurer="Star Health", policy_number="P-123")
    return {"star": star, "other": other, "patient": patient}


def bill_for(hospital, patient, category="insurance"):
    room = ServiceTariff.objects.create(hospital=hospital, code=f"RR{Bill.objects.count()}", name="Room rent", rate=3000)
    adm_kit = ServiceTariff.objects.create(hospital=hospital, code=f"AK{Bill.objects.count()}", name="Admission kit", rate=500, gst_rate=12)
    bill = Bill.objects.create(hospital=hospital, patient=patient, patient_category=category)
    for t, desc in ((room, "Room rent"), (adm_kit, "Admission kit")):
        BillItem.objects.create(bill=bill, description=desc, quantity=1, unit_price=t.rate, total_price=0, tariff=t, gst_rate=t.gst_rate)
    BillItem.objects.create(bill=bill, description="Disposable gloves x 10", quantity=1, unit_price=200, total_price=0)
    BillItem.objects.create(bill=bill, description="Inj. Pantoprazole 40mg", quantity=2, unit_price=150, total_price=0)
    recalculate_bill(bill)
    return bill, adm_kit


# --- PH-1 non-payable items ------------------------------------------------------------

def test_non_payable_lines_are_flagged_and_split_to_the_patient(auth_client, hospital, s):
    irdai = NonPayableList.objects.create(hospital=hospital, name="IRDAI non-payables")
    star_list = NonPayableList.objects.create(hospital=hospital, name="Star exclusions", tpa_company=s["star"])
    care_list = NonPayableList.objects.create(hospital=hospital, name="Care exclusions", tpa_company=s["other"])
    NonPayableItem.objects.create(hospital=hospital, non_payable_list=irdai, keyword="gloves", group="list_1")
    pan = Medicine.objects.create(hospital=hospital, name="Pantoprazole")
    NonPayableItem.objects.create(hospital=hospital, non_payable_list=care_list, medicine=pan)  # another insurer's: ignored
    bill, kit = bill_for(hospital, s["patient"])
    NonPayableItem.objects.create(hospital=hospital, non_payable_list=star_list, tariff=kit, group="list_2")
    recalculate_bill(bill)
    bill.refresh_from_db()
    flagged = {i.description: i.non_payable for i in bill.items.all()}
    assert flagged == {"Room rent": False, "Admission kit": True, "Disposable gloves x 10": True, "Inj. Pantoprazole 40mg": False}
    assert bill.non_payable_amount == Decimal("760.00")  # 500 + 12% GST + 200

    split = auth_client.get(f"/api/v1/billing/bills/{bill.pk}/payer-split/").data
    assert split["payer"] == "Star Health" and split["patient_payable"] == Decimal("760.00")
    assert split["payer_payable"] == bill.net_amount - Decimal("760.00")
    reason = next(l["reason"] for l in split["lines"] if l["description"] == "Admission kit")
    assert reason.startswith("Star exclusions") and "List II" in reason
    pdf = auth_client.get(f"/api/v1/billing/bills/{bill.pk}/download/")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"  # renders the insurer / patient split rows


def test_cash_bills_are_untouched_and_category_lists_apply(hospital, s):
    lst = NonPayableList.objects.create(hospital=hospital, name="IRDAI", applies_to_categories=["insurance", "corporate"])
    NonPayableItem.objects.create(hospital=hospital, non_payable_list=lst, keyword="gloves")
    cash, _ = bill_for(hospital, s["patient"], "general")
    assert cash.non_payable_amount == 0 and not cash.items.filter(non_payable=True).exists()
    corp, _ = bill_for(hospital, s["patient"], "corporate")
    assert corp.non_payable_amount == Decimal("200.00")


def test_non_payable_item_matches_exactly_one_way(auth_client, hospital):
    lst = auth_client.post(f"{TPA}/non-payable-lists/", {"name": "IRDAI"}, format="json").data
    assert lst["tpa_name"] == "All insurers"
    assert auth_client.post(f"{TPA}/non-payable-items/", {"non_payable_list": lst["id"]}, format="json").status_code == 400
    t = ServiceTariff.objects.create(hospital=hospital, code="X", name="X", rate=1)
    assert auth_client.post(f"{TPA}/non-payable-items/", {"non_payable_list": lst["id"], "keyword": "mask", "tariff": t.pk}, format="json").status_code == 400
    assert auth_client.post(f"{TPA}/non-payable-items/", {"non_payable_list": lst["id"], "keyword": "mask"}, format="json").status_code == 201


# --- INS-1 insurance inbox -------------------------------------------------------------

@pytest.fixture
def preauth(hospital, s):
    return PreAuthRequest.objects.create(hospital=hospital, patient=s["patient"], tpa_company=s["star"], policy_number="P-123", claim_amount=80000)


def msg(client, **body):
    return client.post(f"{TPA}/messages/", body, format="json")


def test_query_reply_approval_flow_moves_the_preauth(auth_client, preauth):
    q = msg(auth_client, preauth_request=preauth.pk, direction="in", kind="query", subject="Send previous discharge summary")
    assert q.status_code == 201 and q.data["is_open"] and q.data["respond_by"] == str(timezone.localdate() + timedelta(days=2))
    preauth.refresh_from_db()
    assert preauth.status == "query_raised"
    box = auth_client.get(f"{TPA}/messages/inbox/").data
    assert box["counts"]["open_queries"] == 1 and box["queries"][0]["case"]["type"] == "pre_auth"

    assert msg(auth_client, preauth_request=preauth.pk, direction="out", kind="approval", subject="x").status_code == 400  # we don't approve
    r = msg(auth_client, preauth_request=preauth.pk, direction="out", kind="reply", subject="Summary attached")
    assert r.status_code == 201 and r.data["in_reply_to"] == q.data["id"]
    preauth.refresh_from_db()
    assert preauth.status == "submitted"
    assert auth_client.get(f"{TPA}/messages/inbox/").data["counts"]["open_queries"] == 0

    assert msg(auth_client, preauth_request=preauth.pk, direction="in", kind="approval", subject="Approved").status_code == 400  # amount needed
    msg(auth_client, preauth_request=preauth.pk, direction="in", kind="approval", subject="Approved", amount="60000")
    msg(auth_client, preauth_request=preauth.pk, direction="in", kind="enhancement_ok", subject="Enhanced", amount="15000")
    preauth.refresh_from_db()
    assert preauth.status == "approved" and preauth.approved_amount == Decimal("75000")
    thread = auth_client.get(f"{TPA}/messages/", {"preauth_request": preauth.pk}).data
    assert len(thread["results"] if isinstance(thread, dict) else thread) == 4  # query, reply, approval, enhancement (refused ones aren't kept)
    assert auth_client.patch(f"{TPA}/messages/{q.data['id']}/", {"subject": "edited"}, format="json").status_code == 405


def test_inbox_flags_overdue_queries_waiting_preauths_and_ageing_claims(auth_client, hospital, s, preauth):
    PreAuthRequest.objects.filter(pk=preauth.pk).update(submitted_at=timezone.now() - timedelta(days=5))
    claim = Claim.objects.create(hospital=hospital, patient=s["patient"], tpa_company=s["star"], billed_amount=50000)
    Claim.objects.filter(pk=claim.pk).update(submitted_at=timezone.now() - timedelta(days=45))
    msg(auth_client, claim=claim.pk, direction="in", kind="documents", subject="Original bills", respond_by=str(timezone.localdate() - timedelta(days=1)))
    box = auth_client.get(f"{TPA}/messages/inbox/").data
    assert box["counts"] == {"open_queries": 1, "overdue_queries": 1, "preauths_awaiting": 1, "claims_ageing": 1, "to_review": 0}
    assert box["preauths_awaiting"][0]["waiting_days"] >= 5
    assert auth_client.get(f"{TPA}/messages/inbox/", {"tpa": s["other"].pk}).data["counts"]["open_queries"] == 0

    msg(auth_client, claim=claim.pk, direction="in", kind="settlement", subject="Settled", amount="42000")
    claim.refresh_from_db()
    assert claim.status == "partially_settled" and claim.settled_amount == Decimal("42000")
    assert msg(auth_client, claim=claim.pk, preauth_request=preauth.pk, direction="in", kind="note", subject="x").status_code == 400


# --- INS-2 NHCX pre-auth form ----------------------------------------------------------

def test_nhcx_readiness_bundle_and_submission_through_the_stub(auth_client, hospital, s, preauth):
    r = auth_client.get(f"{TPA}/pre-auth/{preauth.pk}/nhcx/").data
    assert not r["ready"] and "Diagnosis (ICD-10 code)" in r["missing"] and any("participant code" in m for m in r["missing"])
    assert auth_client.post(f"{TPA}/pre-auth/{preauth.pk}/nhcx-submit/").status_code == 400

    from apps.appointments.models import Doctor

    doc = Doctor.objects.create(hospital=hospital, name="Mehta", speciality="General surgery")
    NHCXProfile.objects.create(hospital=hospital, participant_code="1000-provider@hcx", hfr_id="IN2710000001")
    TPACompany.objects.filter(pk=s["star"].pk).update(nhcx_participant_code="1000-payer@hcx")
    upd = auth_client.patch(f"{TPA}/pre-auth/{preauth.pk}/", {
        "member_id": "M-9", "diagnosis_code": "K80.2", "diagnosis": "Calculus of gallbladder", "procedure_name": "Lap cholecystectomy",
        "treating_doctor": doc.pk, "expected_admission_date": str(timezone.localdate() + timedelta(days=3)), "expected_stay_days": 2, "room_category": "private",
        "clinical_summary": "Recurrent biliary colic; USG: multiple calculi.",
        "cost_breakdown": [{"category": "room", "description": "Private room x 2", "amount": 12000}, {"category": "procedure", "description": "Surgery package", "amount": 55000}],
    }, format="json")
    assert upd.status_code == 200, upd.data
    r = auth_client.get(f"{TPA}/pre-auth/{preauth.pk}/nhcx/").data
    assert r["ready"] and r["missing"] == []
    claim = r["bundle"]["entry"][0]["resource"]
    assert claim["resourceType"] == "Claim" and claim["use"] == "preauthorization" and claim["total"]["value"] == 67000.0
    assert claim["diagnosis"][0]["diagnosisCodeableConcept"]["coding"][0]["code"] == "K80.2"
    assert {e["resource"]["resourceType"] for e in r["bundle"]["entry"]} == {"Claim", "Organization", "Patient", "Coverage", "Practitioner"}

    sent = auth_client.post(f"{TPA}/pre-auth/{preauth.pk}/nhcx-submit/")
    assert sent.status_code == 503 and "NHCX is not connected" in sent.data["detail"]  # stub gateway until onboarding
    from apps.abdm.models import NHCXTransaction

    tx = NHCXTransaction.objects.get(pk=sent.data["transaction"])
    assert tx.status == "error" and tx.preauth_request_id == preauth.pk


def test_preauth_amount_from_breakdown_and_validation(auth_client, s):
    base = {"patient": s["patient"].pk, "tpa_company": s["star"].pk, "policy_number": "P-123"}
    assert auth_client.post(f"{TPA}/pre-auth/", base, format="json").status_code == 400
    r = auth_client.post(f"{TPA}/pre-auth/", {**base, "cost_breakdown": [{"category": "room", "amount": 5000}, {"category": "drugs", "amount": "2500.50"}]}, format="json")
    assert r.status_code == 201 and Decimal(r.data["claim_amount"]) == Decimal("7500.50")
    assert auth_client.post(f"{TPA}/pre-auth/", {**base, "cost_breakdown": [{"category": "room", "amount": "lots"}]}, format="json").status_code == 400


def test_list_changes_recheck_open_bills_but_not_paid_ones(auth_client, hospital, s):
    open_bill, _ = bill_for(hospital, s["patient"])
    paid_bill, _ = bill_for(hospital, s["patient"])
    Bill.objects.filter(pk=paid_bill.pk).update(status="paid")
    open_bill.refresh_from_db()
    assert open_bill.non_payable_amount == 0
    lst = auth_client.post(f"{TPA}/non-payable-lists/", {"name": "IRDAI"}, format="json").data
    item = auth_client.post(f"{TPA}/non-payable-items/", {"non_payable_list": lst["id"], "keyword": "gloves"}, format="json").data
    open_bill.refresh_from_db()
    paid_bill.refresh_from_db()
    assert open_bill.non_payable_amount == Decimal("200.00") and paid_bill.non_payable_amount == 0
    auth_client.delete(f"{TPA}/non-payable-items/{item['id']}/")
    open_bill.refresh_from_db()
    assert open_bill.non_payable_amount == 0 and not open_bill.items.filter(non_payable=True).exists()
