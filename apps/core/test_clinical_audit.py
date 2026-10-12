"""Field-level audit trail on clinical records (NABH DAC.2 / MoHFW EHR standards):
who changed what, from what to what, and when — without encrypted values."""
import json

import pytest

from apps.core.models import AuditLog
from apps.patients.models import Patient


def entries(model, pk, action):
    return AuditLog.objects.filter(model_name=model, object_id=str(pk), action=action)


@pytest.mark.django_db
def test_every_field_is_compared_when_none_are_listed(auth_client, user):
    """DAC.2.a: records without an explicit field list still get old/new values."""
    res = auth_client.post("/api/v1/dietary/diet-types/", {"name": "Renal low-protein", "code": "AUD-RLP", "default_calories": 1600}, format="json")
    assert res.status_code == 201, res.data
    pk = res.data["id"]
    created = entries("DietType", pk, "create").get()
    assert created.actor_id == user.pk and created.changes["name"] == "Renal low-protein" and created.changes["default_calories"] == "1600"

    auth_client.patch(f"/api/v1/dietary/diet-types/{pk}/", {"default_calories": 1400}, format="json")
    updated = entries("DietType", pk, "update").get()
    assert updated.changes == {"default_calories": {"old": "1600", "new": "1400"}}

    auth_client.patch(f"/api/v1/dietary/diet-types/{pk}/", {"default_calories": 1400}, format="json")
    assert entries("DietType", pk, "update").count() == 1  # saving without a change adds nothing


@pytest.mark.django_db
def test_prescriptions_are_audited_and_encrypted_content_stays_out_of_the_log(auth_client, user, hospital):
    """DAC.2.a on an e-prescription whose clinical content is encrypted at rest."""
    patient = Patient.objects.create(hospital=hospital, first_name="Asha", last_name="Rao", mobile="9876500011")
    res = auth_client.post("/api/v1/prescriptions/", {"patient": patient.pk, "diagnosis": "Type 2 diabetes", "notes": "Metformin 500"}, format="json")
    assert res.status_code == 201, res.data
    pk = res.data["id"]
    assert entries("Prescription", pk, "create").get().changes["diagnosis"] == "[redacted]"

    auth_client.patch(f"/api/v1/prescriptions/{pk}/", {"diagnosis": "Type 2 diabetes, uncontrolled"}, format="json")
    updated = entries("Prescription", pk, "update").get()
    assert updated.changes["diagnosis"] == {"changed": True} and updated.actor_id == user.pk

    auth_client.delete(f"/api/v1/prescriptions/{pk}/?reason=entered+in+error")
    assert entries("Prescription", pk, "delete").exists()

    log = json.dumps(list(AuditLog.objects.values("changes", "object_repr")))
    assert "diabetes" not in log and "Metformin" not in log
    assert entries("Prescription", pk, "update").get().object_repr == f"Prescription #{pk}"


def test_clinical_viewsets_all_keep_a_field_level_audit_trail():
    """Every viewset that writes clinical records uses the audit mixin, ahead
    of any mixin that would otherwise swallow the delete."""
    from apps.core.viewsets import AuditedModelViewSetMixin, SoftDeleteViewSetMixin
    from apps.icu import views as icu
    from apps.ipd import views as ipd
    from apps.laboratory import views as lab
    from apps.nursing import views as nursing
    from apps.opd import views as opd
    from apps.ot import views as ot
    from apps.patients import views as patients
    from apps.pharmacy import views as pharmacy
    from apps.radiology import views as radiology

    viewsets = [
        opd.EncounterViewSet, opd.VitalsReadingViewSet, opd.ClinicalNoteViewSet, opd.DiagnosisViewSet, opd.InvestigationOrderViewSet,
        ipd.AdmissionViewSet, ipd.WardTransferViewSet, ipd.DoctorProgressNoteViewSet, ipd.DischargeSummaryViewSet,
        lab.LabOrderViewSet, lab.SampleCollectionViewSet, lab.LabResultViewSet,
        radiology.RadiologyOrderViewSet, radiology.RadiologyReportViewSet,
        pharmacy.DispenseRecordViewSet, pharmacy.StockAdjustmentViewSet,
        ot.SurgeryRequestViewSet, ot.OTScheduleViewSet, ot.PreOpChecklistViewSet, ot.OperativeNoteViewSet,
        ot.AnaesthesiaRecordViewSet, ot.ConsumableUsageViewSet, ot.ImplantUsageViewSet,
        icu.ICUAdmissionViewSet, icu.VentilatorLogViewSet, icu.ICUDailyProgressNoteViewSet,
        nursing.NursingNoteViewSet, nursing.MedicationAdministrationViewSet, nursing.IntakeOutputViewSet,
        patients.PatientViewSet, patients.DocumentViewSet, patients.PrescriptionViewSet,
    ]
    for viewset in viewsets:
        mro = viewset.__mro__
        assert AuditedModelViewSetMixin in mro, viewset.__name__
        if SoftDeleteViewSetMixin in mro:
            assert mro.index(AuditedModelViewSetMixin) < mro.index(SoftDeleteViewSetMixin), viewset.__name__
