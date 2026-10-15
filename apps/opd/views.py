from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import ActionPermissionRequired, RequiresClinicalDetailPermission, RoleBasedModelPermissions
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin

from .models import ClinicalNote, Diagnosis, Encounter, InvestigationOrder, OPDTriage, VitalsReading
from .serializers import (
    ClinicalNoteSerializer,
    DiagnosisSerializer,
    EncounterSerializer,
    InvestigationOrderSerializer,
    OPDTriageSerializer,
    VITAL_FIELDS,
    VitalsReadingSerializer,
)

# Every model in this app is clinical content (vitals, notes, diagnoses,
# investigation orders) with no CRM-safe partial view — see
# docs/erp/03-rbac-and-roles.md §2c and
# apps.patients.views.PrescriptionViewSet for the same reasoning applied
# to apps.patients.Prescription. A receptionist role (no
# patients.access_clinical_detail) gets 403 on every endpoint in this app,
# not a filtered view.
CLINICAL_PERMISSION_CLASSES = [IsAuthenticated, RoleBasedModelPermissions, ActionPermissionRequired, RequiresClinicalDetailPermission]


class EncounterViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """Encounters are created automatically at appointment check-in (see
    apps.opd.signals) — this ViewSet is read/admin-correction only in
    practice, but left as a full ModelViewSet rather than ReadOnly so a
    hospital admin can fix a mis-linked encounter without a dbshell."""

    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = EncounterSerializer
    # select_related: EncounterSerializer's patient_name/doctor_name
    # (source="patient.full_name"/"doctor.name")
    queryset = Encounter.objects.select_related("patient", "doctor")
    filterset_fields = ["patient", "doctor", "department", "appointment"]
    assignment_scope_field = "doctor__user"


    @action(detail=True, methods=["post"], url_path="case-type")
    def case_type(self, request, pk=None):
        """POST {case_type: new_complaint | old_complaint} — the doctor corrects
        the case type; the consultation screen reloads the assessment template."""
        from apps.appointments.models import OPDVisit

        enc = self.get_object()
        value = request.data.get("case_type")
        if value not in OPDVisit.CaseType.values:
            return Response({"case_type": "new_complaint | old_complaint"}, status=400)
        visit = getattr(enc.appointment, "opd_visit", None)
        if visit is None:
            return Response({"detail": "This encounter has no front-desk registration."}, status=400)
        visit.case_type = value
        visit.save(update_fields=["case_type"])
        self._log("update", enc)
        return Response(self.get_serializer(enc).data)

class OPDTriageViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """/opd/triage/?encounter=<id> — the nurse records it after check-in;
    the doctor reads and corrects it from the consultation screen."""

    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = OPDTriageSerializer
    queryset = OPDTriage.objects.select_related("vitals", "recorded_by", "updated_by", "encounter__patient", "encounter__doctor__user").order_by("-created_at")
    filterset_fields = ["encounter"]
    http_method_names = ["get", "post", "patch", "put", "head", "options"]

    def _save(self, serializer, **extra):
        from django.db import transaction

        data = serializer.validated_data
        vitals = {f: data.pop(f) for f in VITAL_FIELDS if f in data}
        with transaction.atomic():
            triage = serializer.save(updated_by=self.request.user, **extra)
            self._sync_vitals(triage, vitals)
            self._sync_pain(triage)
            if triage.priority == OPDTriage.Priority.EMERGENCY and ("priority" in serializer.initial_data):
                self._alert_doctor(triage)
        return triage

    def _sync_vitals(self, triage, vitals):
        if not any(v is not None for v in vitals.values()) and triage.vitals_id is None:
            return
        if triage.vitals_id:
            VitalsReading.objects.filter(pk=triage.vitals_id).update(**vitals)
        else:
            reading = VitalsReading.objects.create(hospital_id=triage.hospital_id, encounter=triage.encounter, recorded_by=self.request.user, **vitals)
            OPDTriage.objects.filter(pk=triage.pk).update(vitals=reading)
            triage.vitals = reading

    def _sync_pain(self, triage):
        from apps.clinical import scoring
        from apps.clinical.models import RiskAssessment

        if triage.pain_score is None:
            return
        score, level = scoring.score("numeric_pain", {"score": triage.pain_score})
        if triage.pain_assessment_id:
            RiskAssessment.objects.filter(pk=triage.pain_assessment_id).update(answers={"score": score}, score=score, risk_level=level)
            return
        ra = RiskAssessment.objects.create(hospital_id=triage.hospital_id, patient=triage.encounter.patient, tool="numeric_pain",
                                           answers={"score": score}, score=score, risk_level=level, assessed_by=self.request.user)
        OPDTriage.objects.filter(pk=triage.pk).update(pain_assessment=ra)

    def _alert_doctor(self, triage):
        from apps.clinical.models import ClinicalAlert

        enc = triage.encounter
        ClinicalAlert.objects.create(
            hospital_id=triage.hospital_id, patient=enc.patient, alert_type=ClinicalAlert.AlertType.EARLY_WARNING, severity=ClinicalAlert.Severity.CRITICAL,
            target_user=getattr(enc.doctor, "user", None), title=f"Triage: emergency — see {enc.patient.full_name} now",
            message=triage.chief_complaint or "Marked emergency at OPD triage.", object_id=str(triage.pk),
        )

    def perform_create(self, serializer):
        self._save(serializer, hospital=self.request.user.hospital, recorded_by=self.request.user)

    def perform_update(self, serializer):
        self._save(serializer)


class VitalsReadingViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = VitalsReadingSerializer
    queryset = VitalsReading.objects.all()
    filterset_fields = ["encounter"]
    assignment_scope_field = "encounter__doctor__user"

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, recorded_by=self.request.user)


class ClinicalNoteViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    action_permissions = {"finalize": "opd.finalize_clinicalnote"}
    audited_fields = ("chief_complaints", "history", "examination_findings")
    serializer_class = ClinicalNoteSerializer
    queryset = ClinicalNote.objects.all()
    filterset_fields = ["encounter", "doctor"]
    assignment_scope_field = "doctor__user"

    def perform_create(self, serializer):
        # doctor is stamped from the encounter, not request.user directly
        # — front-desk-adjacent staff may create the placeholder note
        # before the doctor's own login session picks it up, same as
        # walk-in registration doesn't require the doctor to be the one
        # at the keyboard. See apps.patients.views.PrescriptionViewSet for
        # the contrasting case where doctor *is* stamped from request.user
        # (Prescription.doctor FKs straight to User, not to a Doctor
        # directory entry).
        encounter = serializer.validated_data["encounter"]
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, doctor=encounter.doctor)
        self._log("create", serializer.instance)

    @action(detail=True, methods=["post"])
    def finalize(self, request, pk=None):
        note = self.get_object()
        note.finalize(request.user)
        return Response(ClinicalNoteSerializer(note).data)


class DiagnosisViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    action_permissions = {"finalize": "opd.finalize_diagnosis"}
    audited_fields = ("icd_code", "description", "diagnosis_type")
    serializer_class = DiagnosisSerializer
    queryset = Diagnosis.objects.all()
    filterset_fields = ["encounter", "diagnosis_type"]
    assignment_scope_field = "encounter__doctor__user"

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, created_by=self.request.user)
        self._log("create", serializer.instance)

    @action(detail=True, methods=["post"])
    def finalize(self, request, pk=None):
        diagnosis = self.get_object()
        diagnosis.finalize(request.user)
        return Response(DiagnosisSerializer(diagnosis).data)


class InvestigationOrderViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = InvestigationOrderSerializer
    queryset = InvestigationOrder.objects.all()
    filterset_fields = ["encounter", "order_type", "status"]
    assignment_scope_field = "encounter__doctor__user"

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, created_by=self.request.user)
