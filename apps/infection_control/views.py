from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.crud import ClinicalCRUDViewSet, TenantCRUDViewSet, model_serializer

from .models import AntimicrobialApproval, AntimicrobialPolicy, DeviceEpisode, HAIIncident, HandHygieneAudit, StaffExposure

_patient = {"patient_name": serializers.CharField(source="patient.full_name", read_only=True), "patient_uhid": serializers.CharField(source="patient.uhid", read_only=True)}


class DeviceEpisodeViewSet(ClinicalCRUDViewSet):
    serializer_class = model_serializer(DeviceEpisode, read_only=("inserted_by",), extra=_patient)
    queryset = DeviceEpisode.objects.select_related("patient")
    filterset_fields = ["patient", "admission", "device"]
    actor_field = "inserted_by"
    audited_fields = ("device", "inserted_at", "removed_at")

    @action(detail=True, methods=["post"])
    def remove(self, request, pk=None):
        d = self.get_object()
        d.removed_at = timezone.now()
        d.save(update_fields=["removed_at"])
        return Response(self.get_serializer(d).data)


class HAIIncidentViewSet(ClinicalCRUDViewSet):
    serializer_class = model_serializer(HAIIncident, read_only=("reported_by",), extra=_patient)
    queryset = HAIIncident.objects.select_related("patient")
    filterset_fields = ["patient", "infection_type", "status", "ward", "is_mdro"]
    actor_field = "reported_by"
    audited_fields = ("infection_type", "status", "organism")

    @action(detail=False, methods=["get"])
    def rates(self, request):
        """HAI surveillance rates for a period (default: last 30 days)."""
        from apps.quality.kpis import hai_rates, parse_period

        start, end = parse_period(request)
        return Response(hai_rates(request.user.hospital_id, start, end))


class AntimicrobialPolicyViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(AntimicrobialPolicy)
    queryset = AntimicrobialPolicy.objects.all()
    audited_fields = ("version", "is_current", "sections")

    def get_permissions(self):
        if self.action in ("list", "retrieve", "current"):
            from rest_framework.permissions import IsAuthenticated

            return [IsAuthenticated()]
        return super().get_permissions()

    @action(detail=False, methods=["get"])
    def current(self, request):
        policy = self.get_queryset().filter(is_current=True).first()
        return Response(self.get_serializer(policy).data if policy else None)


class AntimicrobialApprovalViewSet(ClinicalCRUDViewSet):
    serializer_class = model_serializer(AntimicrobialApproval, read_only=("requested_by", "status", "decided_by", "decided_at"), extra=_patient)
    queryset = AntimicrobialApproval.objects.select_related("patient")
    filterset_fields = ["patient", "status"]
    actor_field = "requested_by"
    audited_fields = ("status", "drug")

    def _decide(self, request, status):
        a = self.get_object()
        if a.requested_by_id == request.user.pk:
            return Response({"detail": "The requesting prescriber can't approve their own request."}, status=403)
        a.status = status
        a.decided_by = request.user
        a.decided_at = timezone.now()
        a.decision_notes = str(request.data.get("notes", ""))
        a.save(update_fields=["status", "decided_by", "decided_at", "decision_notes"])
        return Response(self.get_serializer(a).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._decide(request, AntimicrobialApproval.Status.APPROVED)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._decide(request, AntimicrobialApproval.Status.REJECTED)


class StaffExposureViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(StaffExposure, read_only=("reported_by",), extra={
        "staff_name": serializers.CharField(source="staff.get_full_name", read_only=True),
    })
    queryset = StaffExposure.objects.select_related("staff")
    filterset_fields = ["staff", "exposure_type", "status"]
    actor_field = "reported_by"
    audited_fields = ("exposure_type", "status", "pep_given")

    def perform_create(self, serializer):
        super().perform_create(serializer)
        e = serializer.instance
        if not e.followups and e.exposure_type in (StaffExposure.ExposureType.NEEDLESTICK, StaffExposure.ExposureType.SPLASH):
            base = e.occurred_at.date()
            e.followups = [
                {"label": "Baseline", "due": str(base), "done": False, "result": ""},
                {"label": "6 weeks", "due": str(base + timedelta(weeks=6)), "done": False, "result": ""},
                {"label": "3 months", "due": str(base + timedelta(days=90)), "done": False, "result": ""},
                {"label": "6 months", "due": str(base + timedelta(days=180)), "done": False, "result": ""},
            ]
            e.save(update_fields=["followups"])


class HandHygieneAuditViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(HandHygieneAudit, read_only=("auditor",))
    queryset = HandHygieneAudit.objects.all()
    filterset_fields = ["ward", "staff_category", "audit_date"]
    actor_field = "auditor"

    def perform_create(self, serializer):
        if serializer.validated_data["compliant"] > serializer.validated_data["opportunities"]:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"compliant": "Cannot exceed opportunities."})
        super().perform_create(serializer)
