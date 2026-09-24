import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.appointments.models import Doctor
from apps.billing.bed_charges import post_bed_charges
from apps.billing.models import BedChargeRule, Bill, BillItem
from apps.facilities.models import Bed, Room, Ward
from apps.finance.models import ServiceTariff
from apps.ipd.models import Admission, BedAllocation, DischargeSummary
from apps.ipd.services import admit_patient, discharge_patient
from apps.patients.models import Patient
from apps.schemes import services
from apps.schemes.models import GovtScheme, SchemeBeneficiary, SchemeCase, SchemeCasePackage, SchemePackage

CSV = "code,name,rate,specialty,expected_los_days\nSG039A,Laparoscopic cholecystectomy,50000,General surgery,3\nMG001A,Acute febrile illness,1800,General medicine,\n"


@pytest.fixture
def s(hospital, department):
    services.install_default_schemes(hospital.pk)
    pmjay = GovtScheme.objects.get(hospital=hospital, code="pmjay")
    cghs = GovtScheme.objects.get(hospital=hospital, code="cghs")
    services.import_packages(pmjay, CSV)
    doctor = Doctor.objects.create(hospital=hospital, department=department, name="Mehta")
    patient = Patient.objects.create(hospital=hospital, first_name="Ramesh", mobile="9876500041")
    ward = Ward.objects.create(hospital=hospital, name="General", ward_type="general")
    bed = Bed.objects.create(hospital=hospital, room=Room.objects.create(hospital=hospital, ward=ward, room_number="1"), bed_number="1")
    room = ServiceTariff.objects.create(hospital=hospital, code="RR", name="Room rent", rate=2000, category_rates={"cghs": 1500})
    lab = ServiceTariff.objects.create(hospital=hospital, code="CBC", name="CBC", rate=400, category_rates={"cghs": 300})
    BedChargeRule.objects.create(hospital=hospital, name="Room", tariff=room)
    adm = admit_patient(hospital=hospital, patient=patient, admitting_doctor=doctor, bed=bed)
    since = timezone.now() - datetime.timedelta(hours=30)
    Admission.objects.filter(pk=adm.pk).update(admitted_at=since)
    BedAllocation.objects.filter(admission=adm).update(allocated_at=since)
    adm.refresh_from_db()
    return {"hospital": hospital, "pmjay": pmjay, "cghs": cghs, "doctor": doctor, "patient": patient, "adm": adm, "lab": lab}


def case_for(s, scheme, **kw):
    ben = SchemeBeneficiary.objects.create(hospital=s["hospital"], patient=s["patient"], scheme=scheme, beneficiary_id=f"{scheme.code}-001", eligibility="eligible")
    return SchemeCase.objects.create(hospital=s["hospital"], beneficiary=ben, admission=s["adm"], **kw)


def add_lab(s, doctor=True):
    bill, _ = post_bed_charges(s["adm"])
    BillItem.objects.create(bill=bill, description="CBC", quantity=1, unit_price=s["lab"].rate, total_price=0, tariff=s["lab"], doctor=s["doctor"] if doctor else None)
    return bill


@pytest.mark.django_db
def test_defaults_and_package_import_are_idempotent(s):
    assert services.install_default_schemes(s["hospital"].pk) == []
    assert GovtScheme.objects.get(pk=s["pmjay"].pk).document_checklist[0].startswith("Beneficiary e-card")
    res = services.import_packages(s["pmjay"], CSV.replace("50000", "52000") + "BAD1,Bad row,abc\n")
    assert (res["created"], res["updated"]) == (0, 2) and res["errors"] == ["Row 4: rate 'abc' is not a number"]
    assert SchemePackage.objects.get(scheme=s["pmjay"], code="SG039A").rate == Decimal("52000")
    with pytest.raises(services.SchemeError):
        services.import_packages(s["pmjay"], "code,title\nX,Y\n")


@pytest.mark.django_db
def test_pmjay_package_is_all_inclusive_and_patient_owes_nothing(s):
    case = case_for(s, s["pmjay"])
    SchemeCasePackage.objects.create(case=case, package=SchemePackage.objects.get(code="SG039A"), rate=Decimal("50000"))
    add_lab(s)
    bill = services.apply_to_bill(case)
    assert bill.patient_category == "pmjay"
    # Itemised: 2 bed-days × 2000 + CBC 400 = 4400, written off by the adjustment; package 50000 billed.
    assert bill.items.get(source="scheme_adjustment").total_price == Decimal("-4400.00")
    assert bill.net_amount == Decimal("50000.00") and case.claim_amount == Decimal("50000.00")
    services.apply_to_bill(case)
    assert bill.items.filter(source="scheme_package").count() == 1  # idempotent

    # Re-posting bed charges keeps the adjustment in step.
    set_to = timezone.now()
    Admission.objects.filter(pk=s["adm"].pk).update(discharged_at=set_to + datetime.timedelta(hours=40))
    s["adm"].refresh_from_db()
    bill, _ = post_bed_charges(s["adm"])
    assert bill.net_amount == Decimal("50000.00")


@pytest.mark.django_db
def test_cghs_rate_list_reprices_to_scheme_rates_with_copay(s):
    s["cghs"].copay_percent = Decimal("20")
    s["cghs"].save()
    case = case_for(s, s["cghs"])
    add_lab(s)
    bill = services.apply_to_bill(case)
    assert bill.items.get(tariff=s["lab"]).unit_price == Decimal("300")
    bill, _ = post_bed_charges(s["adm"])  # bed-days now at the CGHS room rate
    assert bill.net_amount == Decimal("3300.00")  # 2 × 1500 + 300
    case.refresh_from_db()
    assert case.claim_amount == Decimal("2640.00")  # 80%


@pytest.mark.django_db
def test_claim_lifecycle_rules_and_settlement(s, user):
    case = case_for(s, s["pmjay"])
    with pytest.raises(services.SchemeError, match="package"):
        services.transition(case, "preauth_submitted", user)
    with pytest.raises(services.SchemeError, match="pre-auth"):
        services.transition(case, "discharged", user)
    SchemeCasePackage.objects.create(case=case, package=SchemePackage.objects.get(code="SG039A"), rate=Decimal("50000"))
    services.transition(case, "preauth_submitted", user)
    assert case.preauth_amount_requested == Decimal("50000")
    with pytest.raises(services.SchemeError, match="number"):
        services.transition(case, "preauth_approved", user)
    services.transition(case, "preauth_approved", user, preauth_number="PA123")

    add_lab(s)
    DischargeSummary.objects.create(hospital=s["hospital"], admission=s["adm"])
    discharge_patient(s["adm"])  # signal: bed charges, scheme pricing, claim clock
    case.refresh_from_db()
    assert case.status == "discharged" and case.claim_amount == Decimal("50000.00")
    assert case.claim_due_by == timezone.localdate() + datetime.timedelta(days=15)

    with pytest.raises(services.SchemeError, match="Documents"):
        services.transition(case, "claim_submitted", user)
    case.documents = {d: True for d in case.scheme.document_checklist}
    services.transition(case, "claim_submitted", user, claim_number="CL9")
    with pytest.raises(services.SchemeError, match="reason"):
        services.transition(case, "claim_approved", user, amount="45000")
    services.transition(case, "claim_approved", user, amount="45000", deduction_reason="Implant cost disallowed")
    services.transition(case, "settled", user, utr_number="UTR77")
    bill = Bill.objects.get(admission=s["adm"])
    assert bill.payments.get().amount == Decimal("45000") and bill.status == Bill.Status.PARTIALLY_PAID
    assert [h["to"] for h in case.history][-3:] == ["claim_submitted", "claim_approved", "settled"]
    with pytest.raises(services.SchemeError):
        services.transition(case, "claim_submitted", user)


@pytest.mark.django_db
def test_doctor_payout_base_follows_package_revenue(s):
    from apps.finance.payouts import net_base

    case = case_for(s, s["pmjay"])
    SchemeCasePackage.objects.create(case=case, package=SchemePackage.objects.get(code="MG001A"), rate=Decimal("1800"))
    bill = add_lab(s)
    services.apply_to_bill(case)
    lab_line = bill.items.get(tariff=s["lab"])
    # Hospital receives 1800 against 4400 itemised → each line counts at 1800/4400 of its value.
    assert net_base(lab_line) == Decimal("163.64")


@pytest.mark.django_db
def test_scheme_api(auth_client, s):
    ben = auth_client.post("/api/v1/schemes/beneficiaries/", {"patient": s["patient"].pk, "scheme": s["pmjay"].pk, "beneficiary_id": "PMJAY-9"}, format="json")
    assert ben.status_code == 201
    assert auth_client.post(f"/api/v1/schemes/beneficiaries/{ben.data['id']}/verify/", {"eligibility": "eligible"}, format="json").data["eligibility"] == "eligible"
    case = auth_client.post("/api/v1/schemes/cases/", {"beneficiary": ben.data["id"], "admission": s["adm"].pk, "diagnosis": "Cholelithiasis"}, format="json")
    assert case.status_code == 201
    cid = case.data["id"]
    pkg = SchemePackage.objects.get(code="SG039A")
    assert auth_client.post(f"/api/v1/schemes/cases/{cid}/add-package/", {"package": pkg.pk}, format="json").data["packages"][0]["code"] == "SG039A"
    applied = auth_client.post(f"/api/v1/schemes/cases/{cid}/apply-to-bill/")
    assert applied.status_code == 200 and applied.data["claim_amount"] == Decimal("50000.00") and applied.data["patient_payable"] == 0
    bad = auth_client.post(f"/api/v1/schemes/cases/{cid}/transition/", {"to": "settled"}, format="json")
    assert bad.status_code == 400
    doc = auth_client.post(f"/api/v1/schemes/cases/{cid}/documents/", {"document": "Discharge summary", "attached": True}, format="json")
    assert doc.data["documents"] == {"Discharge summary": True}
    pdf = auth_client.get(f"/api/v1/schemes/cases/{cid}/claim-pack/")
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf" and pdf.content[:4] == b"%PDF"
    assert auth_client.get("/api/v1/schemes/cases/dashboard/").data["by_status"]["draft"]["count"] == 1

    other = Patient.objects.create(hospital=s["hospital"], first_name="Other", mobile="9876500042")
    ben2 = SchemeBeneficiary.objects.create(hospital=s["hospital"], patient=other, scheme=s["pmjay"], beneficiary_id="PMJAY-10", eligibility="ineligible")
    res = auth_client.post("/api/v1/schemes/cases/", {"beneficiary": ben2.pk, "admission": s["adm"].pk}, format="json")
    assert res.status_code == 400 and "admission" in res.data or "beneficiary" in res.data
