"""OT parity with MedNet M2.22: OT tariff templates (surgery grade x room
category), the printable anaesthesia chart, and OPD / IPD / emergency
colour coding on the OT board."""
import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role, assign_role
from apps.appointments.models import Doctor
from apps.billing.adjustments import hold
from apps.billing.models import Bill, BillItem
from apps.facilities.models import Bed, Room, Ward
from apps.finance.models import OTTariff, SurgeryGrade
from apps.ipd.models import Admission, BedAllocation
from apps.ipd.services import admit_patient
from apps.patients.models import Patient

from .anaesthesia_chart import vitals_points
from .charges import compute_ot_charges, post_ot_charges
from .models import AnaesthesiaRecord, OTSchedule, SurgeryRequest

OT = "/api/v1/ot"
FIN = "/api/v1/finance"
T0 = timezone.make_aware(datetime.datetime(2026, 10, 12, 9, 0))


@pytest.fixture
def s(hospital, department, user):
    ward = Ward.objects.create(hospital=hospital, name="Ward A")
    room = Room.objects.create(hospital=hospital, ward=ward, room_number="1")
    beds = {t: Bed.objects.create(hospital=hospital, room=room, bed_number=t[:3].upper(), bed_type=t) for t in ("general", "private")}
    major = SurgeryGrade.objects.create(hospital=hospital, code="MAJ", name="Major", sort_order=3)
    OTTariff.objects.create(hospital=hospital, grade=major, room_category="", surgeon_fee=20000, anaesthetist_fee=6000, ot_charge=10000, assistant_fee=4000)
    OTTariff.objects.create(hospital=hospital, grade=major, room_category="private", surgeon_fee=30000, anaesthetist_fee=9000, ot_charge=15000, assistant_fee=6000)
    surgeon = Doctor.objects.create(hospital=hospital, department=department, name="Rao")
    assistant = Doctor.objects.create(hospital=hospital, department=department, name="Iyer")
    anaes_doctor = Doctor.objects.create(hospital=hospital, department=department, name="Das", user=user)
    patient = Patient.objects.create(hospital=hospital, first_name="Meena", last_name="Pillai", mobile="9876500101")
    return {"hospital": hospital, "beds": beds, "major": major, "surgeon": surgeon, "assistant": assistant, "anaes_user": user,
            "anaes_doctor": anaes_doctor, "patient": patient}


def admit(s, bed="general", at=T0 - datetime.timedelta(days=1), **kw):
    adm = admit_patient(hospital=s["hospital"], patient=s["patient"], admitting_doctor=s["surgeon"], bed=s["beds"][bed], **kw)
    Admission.objects.filter(pk=adm.pk).update(admitted_at=at)
    BedAllocation.objects.filter(admission=adm).update(allocated_at=at)
    adm.refresh_from_db()
    return adm


def case(s, admission=None, grade="major", anaesthetist=True, assistant=False, start=T0, **req):
    r = SurgeryRequest.objects.create(hospital=s["hospital"], patient=s["patient"], admission=admission, proposed_procedure="Lap cholecystectomy",
                                      surgery_grade=s[grade] if grade else None, **req)
    return OTSchedule.objects.create(hospital=s["hospital"], surgery_request=r, operation_theatre_room="OT-1", surgeon=s["surgeon"],
                                     assistant_surgeon=s["assistant"] if assistant else None, anaesthetist=s["anaes_user"] if anaesthetist else None,
                                     scheduled_start=start, scheduled_end=start + datetime.timedelta(hours=2))


# --- OT-1 tariff templates ---------------------------------------------------------

def test_tariff_master_crud_rejects_duplicates_and_quotes_with_fallback(auth_client, s):
    g = auth_client.post(f"{FIN}/surgery-grades/", {"code": "MIN", "name": "Minor", "sort_order": 1}, format="json")
    assert g.status_code == 201
    body = {"grade": g.data["id"], "room_category": "general", "surgeon_fee": "5000", "anaesthetist_fee": "1500", "ot_charge": "3000"}
    created = auth_client.post(f"{FIN}/ot-tariffs/", body, format="json")
    assert created.status_code == 201 and Decimal(created.data["total"]) == Decimal("9500")
    assert auth_client.post(f"{FIN}/ot-tariffs/", body, format="json").status_code == 400  # same grade + room
    assert auth_client.post(f"{FIN}/ot-tariffs/", {**body, "room_category": "suite"}, format="json").status_code == 400
    assert auth_client.post(f"{FIN}/ot-tariffs/", {**body, "room_category": "icu", "ot_charge": "-1"}, format="json").status_code == 400

    exact = auth_client.get(f"{FIN}/ot-tariffs/quote/", {"grade": s["major"].pk, "room_category": "private"}).data
    assert exact["matched"] == "room" and Decimal(exact["surgeon_fee"]) == 30000
    fallback = auth_client.get(f"{FIN}/ot-tariffs/quote/", {"grade": s["major"].pk, "room_category": "icu"}).data
    assert fallback["matched"] == "any_room" and Decimal(fallback["surgeon_fee"]) == 20000
    assert auth_client.get(f"{FIN}/ot-tariffs/quote/", {"grade": g.data["id"], "room_category": "private"}).status_code == 404


def test_surgeon_can_read_but_not_change_ot_tariffs(s, hospital):
    surgeon_user = get_user_model().objects.create_user(email="surgeon@test-hospital.example", password="x", hospital=hospital)
    assign_role(surgeon_user, Role.objects.create(hospital=hospital, name="Surgeon", template=Role.Template.SURGEON))
    c = APIClient()
    c.force_authenticate(surgeon_user)
    assert c.get(f"{FIN}/ot-tariffs/").status_code == 200
    t = OTTariff.objects.get(grade=s["major"], room_category="")
    assert c.patch(f"{FIN}/ot-tariffs/{t.pk}/", {"surgeon_fee": "1"}, format="json").status_code == 403


def test_charges_use_the_bed_at_surgery_time_and_credit_each_doctor(s):
    adm = admit(s, "general")
    # moved to a private bed an hour before the surgery
    BedAllocation.objects.filter(admission=adm).update(released_at=T0 - datetime.timedelta(hours=1))
    a = BedAllocation.objects.create(hospital=s["hospital"], admission=adm, bed=s["beds"]["private"])
    BedAllocation.objects.filter(pk=a.pk).update(allocated_at=T0 - datetime.timedelta(hours=1))
    sch = case(s, admission=adm, assistant=True)
    r = compute_ot_charges(sch)
    assert r["room_category"] == "private" and r["total"] == Decimal("60000")
    assert {l["component"]: (l["amount"], l["doctor"]) for l in r["lines"]} == {
        "surgeon": (Decimal("30000"), s["surgeon"]), "anaesthetist": (Decimal("9000"), s["anaes_doctor"]),
        "assistant": (Decimal("6000"), s["assistant"]), "ot": (Decimal("15000"), None),
    }


def test_no_anaesthetist_or_assistant_means_no_fee_and_missing_tariff_is_explained(s, hospital):
    sch = case(s, anaesthetist=False)  # day care, no admission -> any-room row
    r = compute_ot_charges(sch)
    assert r["room_category"] == "" and [l["component"] for l in r["lines"]] == ["surgeon", "ot"]
    minor = SurgeryGrade.objects.create(hospital=hospital, code="MIN", name="Minor")
    s["minor"] = minor
    assert "No OT tariff for Minor" in compute_ot_charges(case(s, grade="minor"))["problem"]
    assert "surgery grade" in compute_ot_charges(case(s, grade=None))["problem"]


def test_posting_is_idempotent_on_the_running_bill(s):
    adm = admit(s)
    sch = case(s, admission=adm)
    bill, _ = post_ot_charges(sch)
    bill2, _ = post_ot_charges(sch)
    assert bill2.pk == bill.pk and bill.admission_id == adm.pk
    assert BillItem.objects.filter(bill=bill, source="ot").count() == 3
    assert Bill.objects.get(pk=bill.pk).total_amount == Decimal("36000")
    sch.refresh_from_db()
    assert sch.charges_posted_at is not None


def test_day_care_surgery_gets_its_own_bill_reused_on_repost(s, auth_client):
    sch = case(s)
    first = auth_client.post(f"{OT}/schedules/{sch.pk}/charges/").data
    again = auth_client.post(f"{OT}/schedules/{sch.pk}/charges/").data
    assert first["bill"] == again["bill"] and Bill.objects.get(pk=first["bill"]).admission_id is None
    assert Decimal(str(again["total"])) == Decimal("36000")
    preview = auth_client.get(f"{OT}/schedules/{sch.pk}/charges/").data
    assert preview["bill"] is None and len(preview["lines"]) == 3


def test_ending_surgery_posts_charges_but_billing_problems_never_block_it(s, auth_client, user):
    adm = admit(s)
    sch = case(s, admission=adm)
    OTSchedule.objects.filter(pk=sch.pk).update(actual_start=T0)
    ended = auth_client.post(f"{OT}/schedules/{sch.pk}/end/").data
    assert ended["status"] == "completed" and ended["charges"]["posted"] and ended["charges_posted_at"]

    held = case(s, admission=adm, start=T0 + datetime.timedelta(hours=3))
    OTSchedule.objects.filter(pk=held.pk).update(actual_start=T0 + datetime.timedelta(hours=3))
    hold(Bill.objects.get(admission=adm), user, "Insurer query")
    r = auth_client.post(f"{OT}/schedules/{held.pk}/end/").data
    assert r["status"] == "completed" and not r["charges"]["posted"] and "on hold" in r["charges"]["problem"]
    assert auth_client.post(f"{OT}/schedules/{held.pk}/charges/").status_code == 400

    ungraded = case(s, grade=None, start=T0 + datetime.timedelta(hours=6))
    OTSchedule.objects.filter(pk=ungraded.pk).update(actual_start=T0 + datetime.timedelta(hours=6))
    r = auth_client.post(f"{OT}/schedules/{ungraded.pk}/end/").data
    assert r["status"] == "completed" and "grade" in r["charges"]["problem"]


def test_cancel_removes_posted_charges_and_refuses_completed_surgery(s, auth_client):
    sch = case(s)
    bill, _ = post_ot_charges(sch)
    assert auth_client.post(f"{OT}/schedules/{sch.pk}/cancel/", {"reason": "Patient unfit"}, format="json").status_code == 200
    assert not BillItem.objects.filter(source="ot", source_ref=f"ot:{sch.pk}").exists()
    assert Bill.objects.get(pk=bill.pk).total_amount == 0
    assert auth_client.post(f"{OT}/schedules/{sch.pk}/charges/").status_code == 400

    done = case(s)
    OTSchedule.objects.filter(pk=done.pk).update(status="completed")
    assert auth_client.post(f"{OT}/schedules/{done.pk}/cancel/", {"reason": "x"}, format="json").status_code == 400


def test_grade_on_request_must_be_this_hospitals_and_active(s, auth_client, other_hospital):
    foreign = SurgeryGrade.objects.create(hospital=other_hospital, code="MAJ", name="Major")
    body = {"patient": s["patient"].pk, "proposed_procedure": "Hernia repair"}
    assert auth_client.post(f"{OT}/surgery-requests/", {**body, "surgery_grade": foreign.pk}, format="json").status_code == 400
    SurgeryGrade.objects.filter(pk=s["major"].pk).update(is_active=False)
    assert auth_client.post(f"{OT}/surgery-requests/", {**body, "surgery_grade": s["major"].pk}, format="json").status_code == 400


# --- OT-3 colour coding by encounter type ------------------------------------------

def test_encounter_is_derived_and_can_be_overridden(s):
    assert case(s).surgery_request.encounter == "opd"
    ipd = admit(s)
    assert case(s, admission=ipd).surgery_request.encounter == "ipd"
    assert case(s, admission=ipd, priority="emergency").surgery_request.encounter == "emergency"
    Admission.objects.filter(pk=ipd.pk).update(admission_type="emergency")
    ipd.refresh_from_db()
    assert case(s, admission=ipd).surgery_request.encounter == "emergency"
    assert case(s, encounter_type="ipd").surgery_request.encounter == "ipd"


def test_board_lists_the_days_cases_with_encounter_and_counts(s, auth_client):
    adm = admit(s)
    case(s)
    case(s, admission=adm, start=T0 + datetime.timedelta(hours=3))
    cancelled = case(s, priority="emergency", start=T0 + datetime.timedelta(hours=5))
    OTSchedule.objects.filter(pk=cancelled.pk).update(status="cancelled", cancellation_reason="No blood")
    case(s, start=T0 + datetime.timedelta(days=1))  # another day
    board = auth_client.get(f"{OT}/schedules/board/", {"date": "2026-10-12"}).data
    assert [c["encounter"] for c in board["cases"]] == ["opd", "ipd", "emergency"]
    assert board["counts"] == {"opd": 1, "ipd": 1, "emergency": 0}  # cancelled cases aren't counted
    assert board["rooms"] == ["OT-1"] and board["cases"][0]["anaesthetist"] == "Dr. Das"
    assert board["cases"][2]["cancellation_reason"] == "No blood"
    assert auth_client.get(f"{OT}/schedules/board/", {"date": "12-10-2026"}).status_code == 400
    req = auth_client.get(f"{OT}/surgery-requests/{board['cases'][1]['surgery_request']}/").data
    assert req["encounter"] == "ipd" and req["surgery_grade_name"] == "Major"


# --- OT-2 anaesthesia chart print --------------------------------------------------

def test_vitals_points_parse_times_bp_and_roll_past_midnight():
    pts = vitals_points([
        {"time": "23:50", "hr": "88", "bp": "130/85", "spo2": 99},
        {"time": "00:05", "hr": 92, "sbp": 118, "dbp": 70},
        {"time": "later", "hr": 90},  # unreadable time: table only
        "junk",
    ], datetime.date(2026, 10, 12))
    assert [t for t, _ in pts] == [23 * 60 + 50, 24 * 60 + 5]
    assert pts[0][1] == {"sbp": 130, "dbp": 85, "hr": 88, "spo2": 99}
    assert pts[1][1] == {"sbp": 118, "dbp": 70, "hr": 92}


def test_anaesthesia_chart_pdf(s, auth_client):
    sch = case(s, admission=admit(s), assistant=True)
    OTSchedule.objects.filter(pk=sch.pk).update(actual_start=T0 + datetime.timedelta(minutes=20), actual_end=T0 + datetime.timedelta(minutes=95))
    rec = AnaesthesiaRecord.objects.create(
        hospital=s["hospital"], ot_schedule=sch, anaesthesia_type="General", anaesthetist=s["anaes_user"], asa_grade="II", airway_device="ETT 7.5",
        induction_at=T0 + datetime.timedelta(minutes=5), reversal_at=T0 + datetime.timedelta(minutes=100),
        vitals=[{"time": f"09:{m:02d}", "hr": 80 + m // 5, "bp": f"{120 + m % 7}/{75 + m % 5}", "spo2": 99, "etco2": 34} for m in range(0, 60, 5)]
        + [{"time": (T0 + datetime.timedelta(minutes=70)).isoformat(), "hr": 84, "bp": "118/72", "temp": "36.6"}],
        drugs=[{"time": "09:05", "drug": "Propofol", "dose": "150 mg", "route": "IV"}, {"time": "09:06", "drug": "Atracurium", "dose": "35 mg", "route": "IV"}],
        fluids=[{"fluid": "Ringer lactate", "volume_ml": 1000}, {"fluid": "NS", "volume_ml": 500}],
        estimated_blood_loss_ml=150, urine_output_ml=200, recovery_aldrete_score=9, shifted_to="Recovery",
    )
    r = auth_client.get(f"{OT}/anaesthesia-records/{rec.pk}/chart/")
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf" and r.content[:4] == b"%PDF"

    empty = AnaesthesiaRecord.objects.create(hospital=s["hospital"], ot_schedule=case(s, start=T0 + datetime.timedelta(hours=4)), anaesthesia_type="Spinal", anaesthetist=s["anaes_user"])
    assert auth_client.get(f"{OT}/anaesthesia-records/{empty.pk}/chart/").content[:4] == b"%PDF"
    assert auth_client.patch(f"{OT}/anaesthesia-records/{empty.pk}/", {"vitals": {"hr": 80}}, format="json").status_code == 400
