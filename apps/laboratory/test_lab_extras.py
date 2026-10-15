"""MedNet M2.22 lab parity: NABL mark, calculated tests, report dispatch, department-wise / merged orders, worklist."""
from datetime import date, timedelta
from unittest import mock

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from apps.clinical.models import ClinicalAlert
from apps.laboratory.lab_extras import FormulaError, dispatch_due, evaluate, next_dispatch_time, report_link
from apps.laboratory.models import LabOrder, LabReportDispatch, LabResult, LabSettings, LabTest
from apps.patients.models import Patient

LAB = "/api/v1/laboratory"


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Meera", mobile="9000000301", email="meera@example.com",
                                  gender="female", date_of_birth=date(1980, 2, 2))


@pytest.fixture
def tests(hospital):
    mk = lambda **kw: LabTest.objects.create(hospital=hospital, **kw)  # noqa: E731
    return {
        "chol": mk(name="Total cholesterol", code="CHOL", department="biochemistry", unit="mg/dL", is_nabl_accredited=True),
        "hdl": mk(name="HDL", code="HDL", department="biochemistry", unit="mg/dL", is_nabl_accredited=True),
        "tg": mk(name="Triglycerides", code="TG", department="biochemistry", unit="mg/dL"),
        "ldl": mk(name="LDL (calculated)", code="LDL", department="biochemistry", unit="mg/dL", formula="{CHOL} - {HDL} - {TG} / 5",
                  ref_high=100, critical_high=300),
        "hb": mk(name="Haemoglobin", code="HB", department="hematology", unit="g/dL", tat_hours=2),
    }


def order(auth_client, patient, *ts, **extra):
    return auth_client.post(f"{LAB}/orders/", {"patient": patient.pk, "ordered_tests": [t.pk for t in ts], **extra}, format="json")


def result(auth_client, order_id, test, value):
    return auth_client.post(f"{LAB}/results/", {"lab_order": order_id, "lab_test": test.pk, "value": value}, format="json")


def verify_all(auth_client, order_id):
    for r in LabResult.objects.filter(lab_order_id=order_id):
        assert auth_client.post(f"{LAB}/results/{r.pk}/verify/").status_code == 200


def test_formula_evaluation_is_safe():
    assert evaluate("{CHOL} - {HDL} - {TG} / 5", {"CHOL": 220, "HDL": 40, "TG": 150}) == 150
    assert evaluate("{CHOL} + {HDL}", {"CHOL": 1}) is None  # input missing
    egfr = "142 * min({CR} / 0.7, 1) ** -0.241 * 0.9938 ** AGE * (1.012 * FEMALE + 1 - FEMALE)"  # CKD-EPI 2021 (creatinine <= 0.7, female)
    assert round(evaluate(egfr, {"CR": 0.6}, age=46, female=True)) == 112
    assert evaluate(egfr, {"CR": 0.6}) is None  # age unknown
    for bad in ("__import__('os')", "{A}.real", "[1, 2]", "'x'", "open('f')", "(lambda: 1)()"):
        with pytest.raises(FormulaError):
            evaluate(bad, {"A": 1})


def test_formula_is_validated_on_the_test_master(auth_client):
    bad = auth_client.post(f"{LAB}/tests/", {"name": "X", "formula": "{A} + __import__('os')"}, format="json")
    assert bad.status_code == 400 and "formula" in bad.json()
    assert auth_client.post(f"{LAB}/tests/", {"name": "Y", "formula": "{A} / {B}"}, format="json").status_code == 201


def test_calculated_test_fills_in_and_raises_a_critical_alert(auth_client, patient, tests, user):
    o = order(auth_client, patient, tests["chol"], tests["hdl"], tests["tg"], tests["ldl"]).json()
    result(auth_client, o["id"], tests["chol"], "220")
    result(auth_client, o["id"], tests["hdl"], "40")
    assert not LabResult.objects.filter(lab_test=tests["ldl"]).exists()  # TG still missing
    result(auth_client, o["id"], tests["tg"], "150")
    ldl = LabResult.objects.get(lab_test=tests["ldl"])
    assert (ldl.value, ldl.flag, ldl.source) == ("150", "high", "calculated")

    # Correcting an input recalculates; a critical calculated value alerts the orderer.
    hdl = LabResult.objects.get(lab_test=tests["hdl"])
    auth_client.patch(f"{LAB}/results/{hdl.pk}/", {"value": "-100"}, format="json")
    ldl.refresh_from_db()
    assert (ldl.value, ldl.flag) == ("290", "high")
    chol = LabResult.objects.get(lab_test=tests["chol"])
    auth_client.patch(f"{LAB}/results/{chol.pk}/", {"value": "330"}, format="json")
    ldl.refresh_from_db()
    assert (ldl.value, ldl.flag) == ("400", "critical")
    assert ClinicalAlert.objects.filter(patient=patient, alert_type="critical_result", target_user=user).exists()


def test_nabl_mark_only_with_accredited_tests_and_valid_certificate(auth_client, patient, tests, hospital):
    from apps.laboratory.workflow import render_lab_report

    s = LabSettings.for_hospital(hospital.pk)
    s.nabl_certificate_number, s.nabl_valid_until = "MC-1234", timezone.localdate() + timedelta(days=30)
    s.save()
    with mock.patch("reportlab.platypus.Paragraph", wraps=__import__("reportlab.platypus", fromlist=["Paragraph"]).Paragraph) as para:
        a = order(auth_client, patient, tests["chol"], tests["tg"]).json()
        result(auth_client, a["id"], tests["chol"], "180")
        result(auth_client, a["id"], tests["tg"], "100")
        render_lab_report(LabOrder.objects.get(pk=a["id"]))
        text = " ".join(str(c.args[0]) for c in para.call_args_list)
    assert "Certificate No. MC-1234" in text and "Tests without * are outside the scope" in text

    b = order(auth_client, patient, tests["hb"]).json()
    result(auth_client, b["id"], tests["hb"], "13")
    with mock.patch("reportlab.platypus.Paragraph", wraps=__import__("reportlab.platypus", fromlist=["Paragraph"]).Paragraph) as para:
        render_lab_report(LabOrder.objects.get(pk=b["id"]))
        assert "MC-1234" not in " ".join(str(c.args[0]) for c in para.call_args_list)  # nothing accredited on it

    s.nabl_valid_until = timezone.localdate() - timedelta(days=1)
    s.save()
    assert not LabSettings.for_hospital(hospital.pk).nabl_active


def test_split_by_department_and_merge_into_a_recent_open_order(auth_client, patient, tests, hospital):
    s = LabSettings.for_hospital(hospital.pk)
    s.split_by_department, s.merge_window_minutes = True, 30
    s.save()
    res = order(auth_client, patient, tests["chol"], tests["hb"]).json()
    assert sorted(o["department"] for o in res["orders"]) == ["biochemistry", "hematology"] and not res["merged"]
    again = order(auth_client, patient, tests["hdl"]).json()
    bio = next(o for o in res["orders"] if o["department"] == "biochemistry")
    assert again["merged"] and again["id"] == bio["id"]
    assert set(LabOrder.objects.get(pk=bio["id"]).ordered_tests.values_list("code", flat=True)) == {"CHOL", "HDL"}

    # Once a sample is collected the order is no longer open to merging.
    auth_client.post(f"{LAB}/samples/", {"lab_order": bio["id"], "sample_type": "Serum"}, format="json")
    assert not order(auth_client, patient, tests["tg"]).json()["merged"]
    assert auth_client.get(f"{LAB}/orders/", {"department": "hematology"}).json()["count"] == 1


def test_worklist_is_one_row_per_test(auth_client, patient, tests):
    o = order(auth_client, patient, tests["chol"], tests["hb"]).json()
    auth_client.post(f"{LAB}/samples/", {"lab_order": o["id"], "sample_type": "Blood"}, format="json")
    result(auth_client, o["id"], tests["chol"], "180")
    LabOrder.objects.filter(pk=o["id"]).update(ordered_at=timezone.now() - timedelta(hours=3))
    rows = {r["test_code"]: r for r in auth_client.get(f"{LAB}/orders/worklist/").json()}
    assert rows["CHOL"]["status"] == "resulted" and rows["CHOL"]["value"] == "180"
    assert rows["HB"]["status"] == "collected" and rows["HB"]["overdue"]  # 2 h TAT
    only = auth_client.get(f"{LAB}/orders/worklist/", {"department": "hematology"}).json()
    assert [r["test_code"] for r in only] == ["HB"]
    assert [r["test_code"] for r in auth_client.get(f"{LAB}/orders/worklist/", {"status": "resulted"}).json()] == ["CHOL"]


def test_verified_reports_are_dispatched_together_and_logged(auth_client, patient, tests, hospital):
    s = LabSettings.for_hospital(hospital.pk)
    s.auto_dispatch, s.dispatch_channels, s.dispatch_delay_minutes = True, ["email", "whatsapp"], 15
    s.save()
    a = order(auth_client, patient, tests["chol"]).json()
    b = order(auth_client, patient, tests["hb"]).json()
    for o, t, v in ((a, tests["chol"], "180"), (b, tests["hb"], "13")):
        result(auth_client, o["id"], t, v)
        verify_all(auth_client, o["id"])
    assert LabOrder.objects.get(pk=a["id"]).dispatch_due_at is not None
    assert dispatch_due() == 0  # still within the delay

    with mock.patch("apps.communications.adapters.StubWhatsAppProvider.send", return_value="wa") as wa:
        assert dispatch_due(timezone.now() + timedelta(minutes=16)) == 2
    # one email; both reports merged into one PDF (merge_dispatch_reports is on by default)
    assert len(mail.outbox) == 1 and [a[0] for a in mail.outbox[0].attachments] == [f"Lab_report_{patient.uhid}.pdf"]
    assert wa.call_count == 1 and "/report-link/" in wa.call_args.kwargs["body"]
    assert LabReportDispatch.objects.filter(sent=True).count() == 4
    assert all(o.dispatched_at for o in LabOrder.objects.all())
    assert dispatch_due(timezone.now() + timedelta(hours=1)) == 0  # never twice

    link = report_link(LabOrder.objects.get(pk=a["id"])).split("/api/v1", 1)[1]
    assert APIClient().get(f"/api/v1{link}").content.startswith(b"%PDF")
    assert APIClient().get(f"/api/v1{link[:-4]}abc/").status_code == 404


def test_manual_send_and_failures_are_logged(auth_client, patient, tests):
    o = order(auth_client, patient, tests["hb"]).json()
    result(auth_client, o["id"], tests["hb"], "13")
    assert auth_client.post(f"{LAB}/orders/{o['id']}/dispatch_report/", {"channels": ["email"]}, format="json").status_code == 400  # not verified
    verify_all(auth_client, o["id"])
    assert LabOrder.objects.get(pk=o["id"]).dispatch_due_at is None  # auto-dispatch off by default
    Patient.objects.filter(pk=patient.pk).update(email="")
    res = auth_client.post(f"{LAB}/orders/{o['id']}/dispatch_report/", {"channels": ["email"]}, format="json").json()
    assert res == [{"channel": "email", "recipient": "", "sent": False, "error": "No email address on the patient's record."}]
    assert auth_client.get(f"{LAB}/orders/{o['id']}/dispatches/").json()[0]["automatic"] is False


def test_scheduled_dispatch_times(hospital):
    s = LabSettings.for_hospital(hospital.pk)
    s.dispatch_times = ["10:00", "17:00"]
    morning = timezone.make_aware(timezone.datetime(2026, 10, 14, 9, 0))
    assert timezone.localtime(next_dispatch_time(s, morning)).hour == 10
    evening = timezone.make_aware(timezone.datetime(2026, 10, 14, 18, 0))
    nxt = timezone.localtime(next_dispatch_time(s, evening))
    assert (nxt.day, nxt.hour) == (15, 10)


def test_settings_endpoint(auth_client, restricted_user):
    r = auth_client.put(f"{LAB}/settings/", {"dispatch_times": ["25:00"]}, format="json")
    assert r.status_code == 400, r.content
    assert auth_client.put(f"{LAB}/settings/", {"dispatch_channels": ["fax"]}, format="json").status_code == 400
    res = auth_client.put(f"{LAB}/settings/", {"nabl_certificate_number": "MC-9", "auto_dispatch": True, "dispatch_times": ["17:00", "10:00"]}, format="json").json()
    assert res["nabl_active"] and res["dispatch_times"] == ["10:00", "17:00"]
    restricted = APIClient()
    restricted.force_authenticate(user=restricted_user)
    assert restricted.put(f"{LAB}/settings/", {"auto_dispatch": False}, format="json").status_code == 403
