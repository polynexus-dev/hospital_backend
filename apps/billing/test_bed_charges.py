import datetime
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.appointments.models import Doctor
from apps.billing.bed_charges import compute_bed_charges, post_all_open_admissions, post_bed_charges
from apps.billing.models import BedBillingPolicy, BedChargeRule, Bill, BillItem, Payment
from apps.facilities.models import Bed, Room, Ward
from apps.finance.models import ServiceTariff
from apps.ipd.models import Admission, BedAllocation, DischargeSummary
from apps.ipd.services import admit_patient, discharge_patient
from apps.patients.models import Patient

T0 = timezone.make_aware(datetime.datetime(2026, 9, 1, 22, 0))


@pytest.fixture
def setup(hospital, department):
    general = Ward.objects.create(hospital=hospital, name="General Ward", ward_type="general")
    icu = Ward.objects.create(hospital=hospital, name="ICU", ward_type="icu")
    g_bed = Bed.objects.create(hospital=hospital, room=Room.objects.create(hospital=hospital, ward=general, room_number="101"), bed_number="G1", bed_type="general")
    g_bed2 = Bed.objects.create(hospital=hospital, room=g_bed.room, bed_number="G2", bed_type="general")
    icu_bed = Bed.objects.create(hospital=hospital, room=Room.objects.create(hospital=hospital, ward=icu, room_number="ICU-1"), bed_number="I1", bed_type="icu")
    t = lambda code, name, rate: ServiceTariff.objects.create(hospital=hospital, code=code, name=name, rate=rate, department="Room", category_rates={"private": rate * 2})
    room_g, nursing_g, room_icu = t("RR-G", "Room rent – general", 1000), t("NC-G", "Nursing charges", 300), t("RR-ICU", "ICU bed charges", 5000)
    BedChargeRule.objects.create(hospital=hospital, name="General room", bed_type="general", tariff=room_g)
    BedChargeRule.objects.create(hospital=hospital, name="General nursing", bed_type="general", tariff=nursing_g)
    BedChargeRule.objects.create(hospital=hospital, name="ICU", ward=icu, tariff=room_icu)
    doctor = Doctor.objects.create(hospital=hospital, department=department, name="Mehta")
    patient = Patient.objects.create(hospital=hospital, first_name="Asha", mobile="9876500001")
    return {"hospital": hospital, "g_bed": g_bed, "g_bed2": g_bed2, "icu_bed": icu_bed, "doctor": doctor, "patient": patient}


def admit(s, bed=None, at=T0):
    adm = admit_patient(hospital=s["hospital"], patient=s["patient"], admitting_doctor=s["doctor"], bed=bed or s["g_bed"])
    Admission.objects.filter(pk=adm.pk).update(admitted_at=at)
    BedAllocation.objects.filter(admission=adm).update(allocated_at=at)
    adm.refresh_from_db()
    return adm


def set_discharged(adm, at):
    Admission.objects.filter(pk=adm.pk).update(discharged_at=at)
    BedAllocation.objects.filter(admission=adm, released_at__isnull=True).update(released_at=at)
    adm.refresh_from_db()


def transfer(adm, to_bed, at):
    BedAllocation.objects.filter(admission=adm, released_at__isnull=True).update(released_at=at)
    alloc = BedAllocation.objects.create(hospital=adm.hospital, admission=adm, bed=to_bed)
    BedAllocation.objects.filter(pk=alloc.pk).update(allocated_at=at)


def per_tariff(result):
    return {ln["tariff"].code: (ln["days"], ln["unit_price"]) for ln in result["lines"]}


@pytest.mark.django_db
def test_24h_cycle_uses_grace_hours_and_charges_every_component(setup):
    adm = admit(setup)
    set_discharged(adm, T0 + datetime.timedelta(hours=26))  # 2h into day 2 = within grace
    r = compute_bed_charges(adm)
    assert len(r["days"]) == 1
    assert per_tariff(r) == {"RR-G": (1, Decimal("1000")), "NC-G": (1, Decimal("300"))}

    set_discharged(adm, T0 + datetime.timedelta(hours=27))
    assert len(compute_bed_charges(adm)["days"]) == 2


@pytest.mark.django_db
def test_calendar_day_charges_discharge_day_only_after_checkout_hour(setup):
    BedBillingPolicy.objects.create(hospital=setup["hospital"], cycle="calendar_day", checkout_hour=12)
    adm = admit(setup)  # 1 Sep 22:00
    set_discharged(adm, timezone.make_aware(datetime.datetime(2026, 9, 3, 10, 0)))
    assert [d["date"] for d in compute_bed_charges(adm)["days"]] == [datetime.date(2026, 9, 1), datetime.date(2026, 9, 2)]
    set_discharged(adm, timezone.make_aware(datetime.datetime(2026, 9, 3, 13, 0)))
    assert len(compute_bed_charges(adm)["days"]) == 3


@pytest.mark.django_db
def test_transfer_day_charges_higher_bed_or_longest_per_policy(setup):
    adm = admit(setup)
    transfer(adm, setup["icu_bed"], T0 + datetime.timedelta(hours=40))  # day 2: 16h general, 8h ICU
    set_discharged(adm, T0 + datetime.timedelta(hours=50))
    higher = per_tariff(compute_bed_charges(adm))
    assert higher == {"RR-G": (1, Decimal("1000")), "NC-G": (1, Decimal("300")), "RR-ICU": (1, Decimal("5000"))}

    BedBillingPolicy.objects.create(hospital=setup["hospital"], transfer_day_rule="longest")
    assert per_tariff(compute_bed_charges(adm)) == {"RR-G": (2, Decimal("1000")), "NC-G": (2, Decimal("300"))}


@pytest.mark.django_db
def test_patient_category_rate_and_unpriced_beds_are_reported(setup):
    adm = admit(setup)
    set_discharged(adm, T0 + datetime.timedelta(hours=10))
    assert per_tariff(compute_bed_charges(adm, category="private"))["RR-G"] == (1, Decimal("2000"))

    BedChargeRule.objects.filter(bed_type="general").update(is_active=False)
    r = compute_bed_charges(adm)
    assert r["lines"] == [] and r["unpriced_beds"] == [str(setup["g_bed"])]


@pytest.mark.django_db
def test_posting_is_idempotent_keeps_manual_lines_and_reopens_a_paid_bill(setup):
    adm = admit(setup)
    set_discharged(adm, T0 + datetime.timedelta(hours=30))  # 2 days
    bill, _ = post_bed_charges(adm)
    BillItem.objects.create(bill=bill, description="CBC", quantity=1, unit_price=400, total_price=0)
    post_bed_charges(adm)
    bill, _ = post_bed_charges(adm)
    assert bill.items.filter(source="bed_charge").count() == 2
    assert bill.items.filter(source="").count() == 1
    assert bill.net_amount == Decimal("3000.00")  # 2 days × (1000 + 300) + manual 400

    # Paid in full, then the stay turns out longer → the bill re-opens.
    bill.status = Bill.Status.UNPAID
    bill.save()
    Payment.objects.create(hospital=bill.hospital, bill=bill, amount=Decimal("3000"))
    from apps.billing.services import recalculate_bill

    assert recalculate_bill(bill).status == Bill.Status.PAID
    set_discharged(adm, T0 + datetime.timedelta(hours=60))  # 3 days
    bill, _ = post_bed_charges(adm)
    assert bill.net_amount == Decimal("4300.00")
    assert bill.status == Bill.Status.PARTIALLY_PAID


@pytest.mark.django_db
def test_discharge_posts_final_bed_charges_and_nightly_run_covers_inpatients(setup):
    adm = admit(setup)
    other = admit({**setup, "patient": Patient.objects.create(hospital=setup["hospital"], first_name="Ravi", mobile="9876500002")}, bed=setup["g_bed2"])
    assert post_all_open_admissions(setup["hospital"].pk) == 2
    assert Bill.objects.get(admission=other).items.filter(source="bed_charge").exists()

    DischargeSummary.objects.create(hospital=setup["hospital"], admission=adm)
    discharge_patient(adm)
    bill = Bill.objects.get(admission=adm)
    assert bill.items.filter(source="bed_charge").exists()
    assert bill.net_amount > 0

    # auto_post off → discharge leaves the bill alone
    BedBillingPolicy.objects.create(hospital=setup["hospital"], auto_post=False)
    assert post_all_open_admissions(setup["hospital"].pk) == 0


@pytest.mark.django_db
def test_bed_charge_api_preview_post_and_policy(auth_client, setup):
    adm = admit(setup)
    set_discharged(adm, T0 + datetime.timedelta(hours=30))
    preview = auth_client.get(f"/api/v1/billing/bills/bed-charges/{adm.pk}/")
    assert preview.status_code == 200
    assert preview.data["bed_days"] == 2 and preview.data["total"] == 2600.0 and preview.data["bill"] is None

    posted = auth_client.post(f"/api/v1/billing/bills/bed-charges/{adm.pk}/post/")
    assert posted.status_code == 200 and posted.data["bill"] is not None
    assert Bill.objects.get(pk=posted.data["bill"]).net_amount == Decimal("2600.00")

    assert auth_client.get("/api/v1/billing/bed-billing-policy/").data["cycle"] == "24h"
    assert auth_client.put("/api/v1/billing/bed-billing-policy/", {"cycle": "weekly"}, format="json").status_code == 400
    res = auth_client.put("/api/v1/billing/bed-billing-policy/", {"cycle": "calendar_day", "checkout_hour": 11}, format="json")
    assert res.status_code == 200 and res.data["checkout_hour"] == 11

    other_hospital_client = APIClient()
    assert other_hospital_client.get(f"/api/v1/billing/bills/bed-charges/{adm.pk}/").status_code == 401
