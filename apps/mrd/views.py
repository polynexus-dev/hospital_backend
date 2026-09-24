from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.crud import ClinicalCRUDViewSet, TenantCRUDViewSet, model_serializer

from .models import CodingRecord, FileMovement, ICD10Code, MedicalRecordFile, RecordAudit


class ICD10Serializer(serializers.ModelSerializer):
    class Meta:
        model = ICD10Code
        fields = ["id", "code", "title", "chapter", "is_billable", "snomed_code"]


class ICD10ViewSet(viewsets.ReadOnlyModelViewSet):
    """Search: ?search=dengue or ?search=A90"""

    permission_classes = [IsAuthenticated]
    serializer_class = ICD10Serializer
    queryset = ICD10Code.objects.all()
    search_fields = ["code", "title"]


def completeness(record_file):
    """Automatic part of the record audit — what the system can verify."""
    adm = record_file.admission
    checks = {}
    if adm:
        checks["initial_assessment"] = adm.assessments.filter(kind="initial").exists()
        checks["consent_on_file"] = adm.consents.exists()
        checks["discharge_summary"] = hasattr(adm, "discharge_summary")
        checks["discharge_summary_signed"] = bool(getattr(getattr(adm, "discharge_summary", None), "finalized_at", None))
        checks["progress_notes"] = adm.progress_notes.exists() if hasattr(adm, "progress_notes") else False
        checks["icd_coded"] = hasattr(adm, "coding")
        checks["medication_reconciliation"] = adm.med_reconciliations.exists()
    return checks


class MedicalRecordFileViewSet(ClinicalCRUDViewSet):
    serializer_class = model_serializer(MedicalRecordFile, read_only=("file_number", "received_at", "destroyed_at", "destruction_approved_by"), extra={
        "patient_name": serializers.CharField(source="patient.full_name", read_only=True),
        "patient_uhid": serializers.CharField(source="patient.uhid", read_only=True),
    })
    queryset = MedicalRecordFile.objects.select_related("patient", "admission")
    filterset_fields = ["status", "patient", "is_mlc"]
    search_fields = ["file_number", "rack_location"]
    audited_fields = ("status", "rack_location")

    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        f = self.get_object()
        f.status = MedicalRecordFile.Status.IN_MRD
        f.received_at = timezone.now()
        f.rack_location = request.data.get("rack_location", f.rack_location)
        f.save()
        return Response(self.get_serializer(f).data)

    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        f = self.get_object()
        if f.status != MedicalRecordFile.Status.IN_MRD:
            return Response({"detail": f"File is {f.get_status_display()}."}, status=400)
        if not request.data.get("issued_to") or not request.data.get("purpose"):
            return Response({"detail": "issued_to and purpose are required."}, status=400)
        FileMovement.objects.create(
            hospital_id=f.hospital_id, record_file=f, issued_to=request.data["issued_to"], department=request.data.get("department", ""),
            purpose=request.data["purpose"], issued_by=request.user, due_back=request.data.get("due_back") or (timezone.localdate() + timedelta(days=3)),
        )
        f.status = MedicalRecordFile.Status.ISSUED
        f.save(update_fields=["status"])
        return Response(self.get_serializer(f).data)

    @action(detail=True, methods=["post"])
    def return_file(self, request, pk=None):
        f = self.get_object()
        mv = f.movements.filter(returned_at__isnull=True).first()
        if mv is None:
            return Response({"detail": "File isn't out."}, status=400)
        mv.returned_at = timezone.now()
        mv.received_back_by = request.user
        mv.save()
        f.status = MedicalRecordFile.Status.IN_MRD
        f.save(update_fields=["status"])
        return Response(self.get_serializer(f).data)

    @action(detail=False, methods=["get"])
    def overdue(self, request):
        rows = FileMovement.objects.filter(hospital_id=request.user.hospital_id, returned_at__isnull=True, due_back__lt=timezone.localdate()).select_related("record_file__patient")
        return Response([{"file_number": m.record_file.file_number, "patient": m.record_file.patient.full_name, "issued_to": m.issued_to, "due_back": m.due_back} for m in rows])

    @action(detail=False, methods=["get"])
    def due_for_destruction(self, request):
        qs = self.get_queryset().filter(retention_until__lt=timezone.localdate()).exclude(status=MedicalRecordFile.Status.DESTROYED)
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=True, methods=["post"])
    def destroy_record(self, request, pk=None):
        f = self.get_object()
        if f.retention_until and f.retention_until >= timezone.localdate():
            return Response({"detail": f"Retention period runs until {f.retention_until}."}, status=400)
        f.status = MedicalRecordFile.Status.DESTROYED
        f.destroyed_at = timezone.now()
        f.destruction_approved_by = request.user
        f.save()
        return Response(self.get_serializer(f).data)

    @action(detail=True, methods=["post"])
    def audit(self, request, pk=None):
        f = self.get_object()
        checklist = {**completeness(f), **{k: bool(v) for k, v in (request.data.get("manual") or {}).items()}}
        score = round(100 * sum(checklist.values()) / len(checklist), 1) if checklist else 0
        a = RecordAudit.objects.create(hospital_id=f.hospital_id, record_file=f, checklist=checklist, score_pct=score,
                                       deficiencies=", ".join(k for k, v in checklist.items() if not v), auditor=request.user)
        return Response({"id": a.pk, "score_pct": float(a.score_pct), "checklist": checklist, "deficiencies": a.deficiencies})


class CodingRecordViewSet(ClinicalCRUDViewSet):
    serializer_class = model_serializer(CodingRecord, read_only=("coded_by", "verified_by", "verified_at"), extra={
        "principal_code": serializers.CharField(source="principal_diagnosis.code", read_only=True),
        "principal_title": serializers.CharField(source="principal_diagnosis.title", read_only=True),
    })
    queryset = CodingRecord.objects.select_related("principal_diagnosis", "admission__patient")
    filterset_fields = ["admission", "principal_diagnosis"]
    actor_field = "coded_by"

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        c = self.get_object()
        c.verified_by = request.user
        c.verified_at = timezone.now()
        c.save(update_fields=["verified_by", "verified_at"])
        return Response(self.get_serializer(c).data)

    @action(detail=False, methods=["get"])
    def morbidity(self, request):
        """Morbidity/mortality statistics by principal ICD-10 code."""
        start = request.query_params.get("start") or str(timezone.localdate() - timedelta(days=365))
        qs = self.get_queryset().filter(admission__discharged_at__date__gte=start)
        top = qs.values("principal_diagnosis__code", "principal_diagnosis__title").annotate(n=Count("id"), deaths=Count("id", filter=Q(admission__status="deceased"))).order_by("-n")[:25]
        return Response(list(top))
