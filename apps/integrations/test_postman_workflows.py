"""Runs the Postman collection's "Workflows" folder against the API, so the
request templates we ship are known to work (paths, bodies, chaining).

A tiny runner: substitutes {{variables}}, sends each request with the test
client, checks it succeeded and applies the folder's `save(...)` captures.
The patient-portal folder is skipped — it needs a real SMS OTP.
"""
import datetime
import json
import re
import time
from pathlib import Path

import pytest
from django.utils import timezone

from apps.appointments.models import Doctor
from apps.facilities.models import Bed, Room, Ward
from apps.ipd.models import Admission, BedAllocation
from apps.ipd.services import admit_patient
from apps.patients.models import Patient

COLLECTION = Path(__file__).resolve().parents[3] / "docs" / "postman" / "Hospital-CRM-ERP.postman_collection.json"
# if (EXPR !== undefined && EXPR !== null) save('var', EXPR);
CAPTURE = re.compile(r"if \((.+?) !== undefined && .+?\) save\('(\w+)', ")


def _workflows():
    col = json.loads(COLLECTION.read_text(encoding="utf-8"))
    return next(f for f in col["item"] if f["name"].startswith("00a."))


def _eval(expr, body):
    """The handful of JS capture expressions the workflow templates use."""
    first = lambda xs: xs[0] if xs else None  # noqa: E731
    table = {
        "body.id": lambda b: b.get("id"),
        "body.bill": lambda b: b.get("bill"),
        "body.token": lambda b: b.get("token"),
        "body[0] && body[0].id": lambda b: (first(b) or {}).get("id"),
        "(body.results || body)[0] && (body.results || body)[0].id": lambda b: (first(b.get("results", b) if isinstance(b, dict) else b) or {}).get("id"),
    }
    return table[expr](body)


def _sub(text, variables):
    text = text.replace("{{$timestamp}}", str(int(time.time() * 1000)))
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(variables.get(m.group(1), m.group(0))), text)


@pytest.mark.django_db
def test_workflow_templates_run_end_to_end(auth_client, hospital, department):
    doctor = Doctor.objects.create(hospital=hospital, department=department, name="Mehta")
    patient = Patient.objects.create(hospital=hospital, first_name="Asha", mobile="9876500031")
    ward = Ward.objects.create(hospital=hospital, name="Private Wing", ward_type="private")
    bed = Bed.objects.create(hospital=hospital, room=Room.objects.create(hospital=hospital, ward=ward, room_number="P1"), bed_number="1", bed_type="private")
    admission = admit_patient(hospital=hospital, patient=patient, admitting_doctor=doctor, bed=bed)
    since = timezone.now() - datetime.timedelta(hours=30)
    Admission.objects.filter(pk=admission.pk).update(admitted_at=since)
    BedAllocation.objects.filter(admission=admission).update(allocated_at=since)

    today = timezone.localdate()
    variables = {"period_start": str(today.replace(day=1)), "period_end": str(today)}
    ran = []
    for folder in _workflows()["item"]:
        if "portal" in folder["name"].lower():
            continue
        for item in folder["item"]:
            req = item["request"]
            path = _sub(req["url"]["raw"], variables).replace("{{base_url}}", "")
            raw_body = _sub(req["body"]["raw"], variables) if "body" in req else None
            assert raw_body is None or "{{" not in raw_body, f"{item['name']}: unresolved variable in {raw_body}"
            body = json.loads(raw_body) if raw_body is not None else None
            res = getattr(auth_client, req["method"].lower())(path, body, format="json") if body is not None else getattr(auth_client, req["method"].lower())(path)
            assert res.status_code in (200, 201), f"{folder['name']} › {item['name']}: {res.status_code} {getattr(res, 'data', res.content)[:500] if not hasattr(res, 'data') else res.data}"
            ran.append(item["name"])
            data = res.data if hasattr(res, "data") else None
            for ev in item.get("event", []):
                for line in ev["script"]["exec"]:
                    m = CAPTURE.search(line)
                    if m and data is not None:
                        value = _eval(m.group(1), data)
                        if value is not None:
                            variables[m.group(2)] = value

    # The chain really connected: the admission picked, a statement paid.
    assert variables["admission_id"] == admission.pk
    assert "Mark statement paid" in ran
    from apps.finance.models import DoctorPayout

    assert DoctorPayout.objects.get(pk=variables["payout_id"]).status == "paid"
