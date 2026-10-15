"""MedNet M2.22 lab, second batch: order processing filters and the patient's
pending-investigations view; outsourced reports (held from auto-dispatch,
outside report finalises the test); one merged report per patient."""
from datetime import date

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.laboratory.lab_extras import dispatch_due, schedule_dispatch
from apps.laboratory.merged_report import render_patient_lab_report
from apps.laboratory.models import LabOrder, LabResult, LabSettings, LabTest, OutsourcedLabTest
from apps.patients.models import Patient

O = "/api/v1/laboratory/orders/"


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Avika", last_name="Sharma", gender="female", date_of_birth=date(1988, 3, 3),
                                  mobile="+919000000901", email="avika@example.com")


@pytest.fixture
def tests(hospital):
    return {
        "cbc": LabTest.objects.create(hospital=hospital, name="CBC", code="CBC", department="hematology", unit="", method="Impedance"),
        "sugar": LabTest.objects.create(hospital=hospital, name="Blood sugar (F)", code="FBS", department="biochemistry", unit="mg/dl", reference_range="70-100", method="GOD-POD"),
        "urea": LabTest.objects.create(hospital=hospital, name="Blood urea", code="UREA", department="biochemistry", unit="mg/dl", method="Urease"),
        "vitd": LabTest.objects.create(hospital=hospital, name="Vitamin D", code="VITD", department="biochemistry", unit="ng/ml", is_outsourced=True),
    }


def order(hospital, patient, *tests, status=LabOrder.Status.ORDERED):
    o = LabOrder.objects.create(hospital=hospital, patient=patient, status=status)
    o.ordered_tests.set(tests)
    return o


def result(o, test, value, user=None, final=False):
    r = LabResult.objects.create(hospital=o.hospital, lab_order=o, lab_test=test, value=value)
    if final:
        r.finalize(user)
    return r


def test_order_processing_filters(auth_client, hospital, patient, tests):
    fresh = order(hospital, patient, tests["cbc"], tests["sugar"])
    half = order(hospital, patient, tests["cbc"], tests["sugar"])
    result(half, tests["cbc"], "12.1")
    ready = order(hospital, patient, tests["urea"])
    result(ready, tests["urea"], "28")
    ids = lambda state: {r["id"] for r in auth_client.get(f"{O}?processing={state}").data["results"]}  # noqa: E731
    assert ids("not_started") == {fresh.pk} and ids("in_process") == {half.pk} and ids("results_ready") == {ready.pk}
    listed = {r["id"]: r["processing_state"] for r in auth_client.get(O).data["results"]}
    assert listed[fresh.pk] == "not_started" and listed[half.pk] == "in_process" and listed[ready.pk] == "results_ready"


def test_patient_pending_investigations(auth_client, hospital, patient, tests, user):
    o = order(hospital, patient, tests["cbc"], tests["sugar"])
    result(o, tests["cbc"], "11.9")
    done = order(hospital, patient, tests["urea"], status=LabOrder.Status.VERIFIED)
    result(done, tests["urea"], "30", user, final=True)
    r = auth_client.get(f"{O}patient-status/?patient={patient.pk}")
    assert r.status_code == 200
    stages = {x["test_name"]: x["status"] for x in r.data["pending"]}
    assert stages == {"CBC": "resulted", "Blood sugar (F)": "pending_collection"}
    assert r.data["counts"]["verified"] == 1 and r.data["reporting"][0]["order"] == done.pk and r.data["reporting"][0]["verified_by"]
    assert auth_client.get(f"{O}patient-status/?patient=0").status_code == 400


def test_outsourced_reports_held_from_auto_dispatch(hospital, patient, tests, user):
    s = LabSettings.for_hospital(hospital.pk)
    s.auto_dispatch, s.dispatch_channels, s.dispatch_delay_minutes, s.dispatch_outsourced = True, ["email"], 0, False
    s.save()
    o = order(hospital, patient, tests["vitd"], status=LabOrder.Status.VERIFIED)
    OutsourcedLabTest.objects.create(hospital=hospital, lab_order=o, lab_test=tests["vitd"], external_lab="SRL")
    schedule_dispatch(o)
    o.refresh_from_db()
    assert o.dispatch_due_at is None  # held for staff
    own = order(hospital, patient, tests["urea"], status=LabOrder.Status.VERIFIED)
    result(own, tests["urea"], "30", user, final=True)
    schedule_dispatch(own)
    own.refresh_from_db()
    assert own.dispatch_due_at is not None
    LabOrder.objects.filter(pk=o.pk).update(dispatch_due_at=timezone.now())  # scheduled before the setting changed
    mail.outbox.clear()
    dispatch_due(now=timezone.now() + timezone.timedelta(minutes=1))
    o.refresh_from_db()
    assert o.dispatched_at is None and o.dispatch_due_at is None
    assert LabOrder.objects.get(pk=own.pk).dispatched_at is not None


def test_outside_report_upload_finalises_the_test(auth_client, hospital, patient, tests):
    o = order(hospital, patient, tests["vitd"], status=LabOrder.Status.PROCESSING)
    out = OutsourcedLabTest.objects.create(hospital=hospital, lab_order=o, lab_test=tests["vitd"], external_lab="SRL", status="sent")
    url = f"/api/v1/laboratory/outsourced-tests/{out.pk}/record_result/"
    assert auth_client.post(url, {"report_type": "outside_report"}, format="multipart").status_code == 400  # needs the file
    pdf = SimpleUploadedFile("srl.pdf", b"%PDF-1.4 outside", content_type="application/pdf")
    r = auth_client.post(url, {"report_type": "outside_report", "result_file": pdf}, format="multipart")
    assert r.status_code == 200, r.data
    res = LabResult.objects.get(lab_order=o, lab_test=tests["vitd"])
    assert res.finalized_at is not None and res.value == "See outside report"
    o.refresh_from_db()
    assert o.status == LabOrder.Status.VERIFIED


def test_merged_report_and_email(auth_client, hospital, patient, tests, user):
    a = order(hospital, patient, tests["cbc"], status=LabOrder.Status.VERIFIED)
    result(a, tests["cbc"], "12.0", user, final=True)
    b = order(hospital, patient, tests["sugar"], tests["urea"], status=LabOrder.Status.VERIFIED)
    result(b, tests["sugar"], "111", user, final=True)
    result(b, tests["urea"], "12", user, final=True)
    pdf = render_patient_lab_report([a, b])
    assert pdf.startswith(b"%PDF")
    r = auth_client.get(f"{O}patient-report/?patient={patient.pk}")
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf"
    from apps.laboratory.lab_extras import send_reports

    mail.outbox.clear()
    send_reports([a, b], ["email"])
    names = [att[0] for att in mail.outbox[0].attachments]
    assert names == [f"Lab_report_{patient.uhid}.pdf"]  # one merged document
    s = LabSettings.for_hospital(hospital.pk)
    s.merge_dispatch_reports = False
    s.save()
    mail.outbox.clear()
    LabOrder.objects.filter(pk__in=[a.pk, b.pk]).update(dispatched_at=None)
    send_reports([LabOrder.objects.get(pk=a.pk), LabOrder.objects.get(pk=b.pk)], ["email"])
    assert len(mail.outbox[0].attachments) == 2
