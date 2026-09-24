from collections import Counter
from datetime import date

from django.db import transaction
from django.db.models import Count
from django.db.models.functions import TruncMonth
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.crud import TenantCRUDViewSet, model_serializer

from . import kpis
from .models import (
    ChecklistRun,
    ChecklistTemplate,
    CodeActivation,
    CodeResponse,
    EmergencyCode,
    KPIManualEntry,
    KPISnapshot,
    MedicationError,
    MockDrill,
    SafetyIncident,
)

_patient = {
    "patient_name": serializers.CharField(source="patient.full_name", read_only=True, default=None),
    "patient_uhid": serializers.CharField(source="patient.uhid", read_only=True, default=None),
}


class SafetyIncidentSerializer(model_serializer(SafetyIncident, read_only=("reported_by", "is_sentinel", "closed_at"), extra=_patient)):
    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.is_anonymous:
            data["reported_by"] = None
        return data


class SafetyIncidentViewSet(TenantCRUDViewSet):
    """COP.8.c — any staff member can report; everyone sees the register."""

    serializer_class = SafetyIncidentSerializer
    queryset = SafetyIncident.objects.select_related("patient")
    filterset_fields = ["incident_type", "harm", "is_sentinel", "status", "patient"]
    search_fields = ["description", "location"]
    actor_field = "reported_by"
    audited_fields = ("status", "harm", "root_cause", "corrective_action", "preventive_action")

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        inc = self.get_object()
        if inc.is_sentinel and not inc.root_cause:
            return Response({"root_cause": "A sentinel event cannot be closed without a root-cause analysis."}, status=400)
        inc.status = SafetyIncident.Status.CLOSED
        inc.closed_at = timezone.now()
        inc.save(update_fields=["status", "closed_at"])
        return Response(self.get_serializer(inc).data)

    @action(detail=False, methods=["get"])
    def dashboard(self, request):
        start, end = kpis.parse_period(request, default_days=180)
        qs = self.get_queryset().filter(occurred_at__date__range=(start, end))
        return Response({
            "total": qs.count(),
            "sentinel": qs.filter(is_sentinel=True).count(),
            "open": qs.exclude(status=SafetyIncident.Status.CLOSED).count(),
            "by_type": dict(qs.values_list("incident_type").annotate(n=Count("id"))),
            "by_harm": dict(qs.values_list("harm").annotate(n=Count("id"))),
            "by_month": [{"month": r["m"].strftime("%Y-%m"), "count": r["n"]} for r in qs.annotate(m=TruncMonth("occurred_at")).values("m").annotate(n=Count("id")).order_by("m")],
        })


class MedicationErrorViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(MedicationError, read_only=("detected_by", "incident"), extra=_patient)
    queryset = MedicationError.objects.select_related("patient")
    filterset_fields = ["stage", "error_type", "category", "patient"]
    actor_field = "detected_by"
    audited_fields = ("stage", "error_type", "category")

    def perform_create(self, serializer):
        with transaction.atomic():
            super().perform_create(serializer)
            me = serializer.instance
            harm = {"A": "near_miss", "B": "near_miss", "C": "no_harm", "D": "no_harm", "E": "mild", "F": "moderate", "G": "severe", "H": "severe", "I": "death"}[me.category]
            me.incident = SafetyIncident.objects.create(
                hospital_id=me.hospital_id, incident_type=SafetyIncident.IncidentType.MEDICATION_ERROR, harm=harm,
                patient=me.patient, occurred_at=me.occurred_at, reported_by=self.request.user,
                description=f"{me.get_error_type_display()} at {me.get_stage_display().lower()}: {me.medication}. {me.description}".strip(),
            )
            me.save(update_fields=["incident"])

    @action(detail=False, methods=["get"])
    def dashboard(self, request):
        """MOM.4.c — consolidated, trendable medication-error analytics."""
        start, end = kpis.parse_period(request, default_days=365)
        qs = self.get_queryset().filter(occurred_at__date__range=(start, end))
        top = Counter(qs.values_list("medication", flat=True)).most_common(10)
        rate = kpis.compute(request.user.hospital_id, start, end, codes=["K04"])[0]
        return Response({
            "period": {"start": start, "end": end},
            "total": qs.count(),
            "near_misses": qs.filter(category__in=["A", "B"]).count(),
            "harmful": qs.filter(category__in=["E", "F", "G", "H", "I"]).count(),
            "error_rate": rate,
            "by_stage": dict(qs.values_list("stage").annotate(n=Count("id"))),
            "by_type": dict(qs.values_list("error_type").annotate(n=Count("id"))),
            "by_category": dict(qs.values_list("category").annotate(n=Count("id"))),
            "by_month": [{"month": r["m"].strftime("%Y-%m"), "count": r["n"]} for r in qs.annotate(m=TruncMonth("occurred_at")).values("m").annotate(n=Count("id")).order_by("m")],
            "top_medications": [{"medication": m, "count": c} for m, c in top],
        })


class EmergencyCodeViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(EmergencyCode)
    queryset = EmergencyCode.objects.prefetch_related("responders")
    filterset_fields = ["is_active"]
    audited_fields = ("code", "meaning", "responder_roles", "is_active")

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return super().get_permissions()


class CodeActivationSerializer(model_serializer(CodeActivation, read_only=("activated_by", "status", "closed_at"))):
    code_name = serializers.CharField(source="code.code", read_only=True)
    code_color = serializers.CharField(source="code.color", read_only=True)
    responses = serializers.SerializerMethodField()
    first_response_minutes = serializers.SerializerMethodField()

    def get_responses(self, obj):
        return [{"staff": r.staff.get_full_name(), "role": r.role_in_response, "responded_at": r.responded_at, "notes": r.notes} for r in obj.responses.select_related("staff")]

    def get_first_response_minutes(self, obj):
        first = obj.responses.order_by("responded_at").first()
        return round((first.responded_at - obj.activated_at).total_seconds() / 60, 1) if first else None


class CodeActivationViewSet(TenantCRUDViewSet):
    """COP.4.d — any signed-in staff can call a code; responders are alerted."""

    serializer_class = CodeActivationSerializer
    queryset = CodeActivation.objects.select_related("code")
    filterset_fields = ["code", "status", "is_drill"]
    actor_field = "activated_by"
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        super().perform_create(serializer)
        act = serializer.instance
        from apps.accounts.models import User
        from apps.clinical.models import ClinicalAlert

        responders = set(act.code.responders.all())
        if act.code.responder_roles:
            responders |= set(User.objects.filter(hospital_id=act.hospital_id, is_active=True, role__template__in=act.code.responder_roles))
        for user in responders:
            ClinicalAlert.objects.create(
                hospital_id=act.hospital_id, patient=act.patient, alert_type=ClinicalAlert.AlertType.CDSS, severity=ClinicalAlert.Severity.CRITICAL,
                title=f"{'DRILL — ' if act.is_drill else ''}{act.code.code} at {act.location}",
                message=f"{act.code.meaning}. Respond to {act.location}. {act.code.protocol[:500]}", target_user=user,
                object_id=str(act.pk),
            )

    @action(detail=True, methods=["post"])
    def respond(self, request, pk=None):
        act = self.get_object()
        resp, _ = CodeResponse.objects.get_or_create(
            hospital_id=act.hospital_id, activation=act, staff=request.user,
            defaults={"role_in_response": str(request.data.get("role", ""))[:60], "notes": str(request.data.get("notes", ""))},
        )
        return Response(self.get_serializer(act).data)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        act = self.get_object()
        act.status = CodeActivation.Status.CLOSED
        act.closed_at = timezone.now()
        act.outcome = str(request.data.get("outcome", ""))
        act.save(update_fields=["status", "closed_at", "outcome"])
        return Response(self.get_serializer(act).data)


class MockDrillViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(MockDrill, read_only=("conducted_by",))
    queryset = MockDrill.objects.all()
    filterset_fields = ["code"]
    actor_field = "conducted_by"


class ChecklistTemplateViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(ChecklistTemplate)
    queryset = ChecklistTemplate.objects.all()
    filterset_fields = ["purpose", "is_active"]
    audited_fields = ("name", "items", "is_active")


class ChecklistRunViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(ChecklistRun, read_only=("completed_by", "all_passed"), extra={
        "template_name": serializers.CharField(source="template.name", read_only=True),
    })
    queryset = ChecklistRun.objects.select_related("template")
    filterset_fields = ["template", "all_passed"]
    actor_field = "completed_by"


class KPIManualEntryViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(KPIManualEntry, read_only=("entered_by",))
    queryset = KPIManualEntry.objects.all()
    filterset_fields = ["kpi_code"]
    actor_field = "entered_by"
    audited_fields = ("numerator", "denominator")

    def perform_create(self, serializer):
        if serializer.validated_data["kpi_code"] not in kpis.ALL_KPIS:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"kpi_code": "Unknown KPI code."})
        super().perform_create(serializer)


class KPIView(APIView):
    """IMS.2.a/b — GET ?start=&end=&kind=nabh|dhs[&format=json|csv|xml|xlsx|pdf]"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        start, end = kpis.parse_period(request)
        kind = request.query_params.get("kind")
        codes = [c for c, k in kpis.ALL_KPIS.items() if not kind or k.kind == kind]
        rows = kpis.compute(request.user.hospital_id, start, end, codes=codes)
        fmt = request.query_params.get("export")
        if fmt:
            try:
                body, ctype, ext = kpis.export(rows, fmt, hospital_name=getattr(request.user.hospital, "name", ""), start=start, end=end)
            except ValueError as exc:
                return Response({"detail": str(exc)}, status=400)
            resp = HttpResponse(body, content_type=ctype)
            resp["Content-Disposition"] = f'attachment; filename="nabh-kpis-{start}-{end}.{ext}"'
            return resp
        return Response({"period": {"start": start, "end": end}, "kpis": rows, "definitions": [
            {"code": k.code, "name": k.name, "unit": k.unit, "standard": k.standard, "kind": k.kind, "manual": k.manual} for k in kpis.ALL_KPIS.values()
        ]})


class KPIPublishView(APIView):
    """IMS.2.c — snapshot + publish a quarter (default: previous quarter)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from apps.core.permissions import CanReviewEmergencyAccess

        if not CanReviewEmergencyAccess().has_permission(request, self):
            return Response({"detail": "Only administrators can publish KPIs."}, status=403)
        if request.data.get("start") and request.data.get("end"):
            start, end = date.fromisoformat(request.data["start"]), date.fromisoformat(request.data["end"])
        else:
            start, end = kpis.previous_quarter()
        snaps = kpis.snapshot(request.user.hospital_id, start, end, publish=True)
        return Response({"period": {"start": start, "end": end}, "published": len(snaps)})

    def get(self, request):
        qs = KPISnapshot.objects.filter(hospital_id=request.user.hospital_id, is_published=True)
        periods = qs.values("period_start", "period_end").annotate(n=Count("id")).order_by("-period_start")
        return Response(list(periods))
