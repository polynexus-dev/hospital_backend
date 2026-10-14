"""Required-documents checklist at registration, admission, diagnostics."""
from datetime import timedelta
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.patients.models import Document, DocumentRequirement, Patient
from apps.portal.tests import _portal_client


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Meena", last_name="Rao", mobile="9811100071")


def pdf(name="scan.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 x", content_type="application/pdf")


def stage(auth_client, patient, name):
    return auth_client.get(f"/api/v1/patients/{patient.pk}/document-checklist/?stage={name}").json()["stages"][0]


def test_defaults_and_missing_items(auth_client, patient):
    reg = stage(auth_client, patient, "registration")
    assert reg["missing_names"] == ["Photo ID (Aadhaar, PAN, passport, voter ID or driving licence)"]  # not insured: no insurance card
    Patient.objects.filter(pk=patient.pk).update(insurance_provider="Star Health")
    assert stage(auth_client, patient, "registration")["missing_required"] == 2


def test_id_proof_uploaded_once_counts_everywhere(auth_client, patient):
    res = auth_client.post("/api/v1/documents/", {"patient": patient.pk, "category": "id_proof", "title": "Aadhaar", "file": pdf()}, format="multipart")
    assert res.status_code == 201, res.json()
    assert stage(auth_client, patient, "registration")["complete"]
    adm = stage(auth_client, patient, "admission")
    assert [i["satisfied"] for i in adm["items"] if i["category"] == "id_proof"] == [True]
    assert adm["missing_names"] == ["Signed general consent for admission"]


def test_consent_must_be_recent_and_linked(auth_client, hospital, patient):
    stage(auth_client, patient, "admission")  # defaults are created on first use
    consent = DocumentRequirement.objects.get(hospital=hospital, stage="admission", category="consent")
    old = Document.objects.create(hospital=hospital, patient=patient, category="consent", file=pdf(), requirement=consent)
    Document.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=60))
    assert "Signed general consent for admission" in stage(auth_client, patient, "admission")["missing_names"]
    auth_client.post("/api/v1/documents/", {"patient": patient.pk, "requirement": consent.pk, "file": pdf()}, format="multipart")
    item = next(i for i in stage(auth_client, patient, "admission")["items"] if i["requirement"] == consent.pk)
    assert item["satisfied"] and item["document"]["title"]


def test_request_documents_messages_the_patient_with_the_portal_link(auth_client, patient):
    with mock.patch("apps.clinical.notify.notify_patient", return_value=mock.Mock(channel="whatsapp")) as notify:
        res = auth_client.post(f"/api/v1/patients/{patient.pk}/request-documents/", {"stage": "registration"}, format="json")
    assert res.status_code == 200 and res.json()["missing"][0].startswith("Photo ID")
    purpose, ctx = notify.call_args.args[1], notify.call_args.args[2]
    assert purpose == "documents_needed" and ctx["link"].endswith("/portal") and ctx["documents"].startswith("Photo ID")


def test_patient_uploads_from_the_portal(hospital, patient, monkeypatch):
    c, _ = _portal_client(hospital, patient.mobile, monkeypatch)
    needed = c.get(f"/api/v1/portal/documents/?patient={patient.pk}").json()["stages"]
    reg = next(s for s in needed if s["stage"] == "registration")
    req = next(i for i in reg["items"] if not i["satisfied"])["requirement"]
    assert c.post("/api/v1/portal/documents/", {"patient": patient.pk, "requirement": req, "file": SimpleUploadedFile("x.exe", b"MZ")}, format="multipart").status_code == 400
    res = c.post("/api/v1/portal/documents/", {"patient": patient.pk, "requirement": req, "file": pdf("aadhaar.pdf")}, format="multipart")
    assert res.status_code == 201
    doc = Document.objects.get()
    assert doc.uploaded_via_portal and doc.category == "id_proof" and doc.requirement_id == req


def test_hospital_admins_edit_the_list_others_only_read(auth_client, hospital, restricted_user):
    from rest_framework.test import APIClient

    restricted_client = APIClient()
    restricted_client.force_authenticate(user=restricted_user)
    rows = auth_client.get("/api/v1/document-requirements/?stage=radiology").json()["results"]
    assert {r["name"] for r in rows} >= {"Doctor's requisition / prescription"}
    created = auth_client.post("/api/v1/document-requirements/", {"stage": "radiology", "name": "Contrast consent", "category": "consent", "required": True, "valid_days": 7}, format="json")
    assert created.status_code == 201, created.json()
    assert restricted_client.post("/api/v1/document-requirements/", {"stage": "radiology", "name": "x", "category": "other"}, format="json").status_code == 403
