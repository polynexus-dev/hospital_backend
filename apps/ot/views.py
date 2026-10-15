from datetime import date, datetime, time, timedelta

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import ActionPermissionRequired, RequiresClinicalDetailPermission, RoleBasedModelPermissions
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin

from .models import (
    AnaesthesiaRecord,
    ConsumableUsage,
    ImplantUsage,
    OperativeNote,
    OTSchedule,
    PreOpChecklist,
    SurgeryRequest,
)
from .workflow import OTScheduleWorkflowMixin
from .serializers import (
    AnaesthesiaRecordSerializer,
    ConsumableUsageSerializer,
    ImplantUsageSerializer,
    OperativeNoteSerializer,
    OTScheduleSerializer,
    PreOpChecklistSerializer,
    SurgeryRequestSerializer,
)

CLINICAL_PERMISSION_CLASSES = [IsAuthenticated, RoleBasedModelPermissions, ActionPermissionRequired, RequiresClinicalDetailPermission]


class SurgeryRequestViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = SurgeryRequestSerializer
    queryset = SurgeryRequest.objects.all()
    filterset_fields = ["patient", "admission", "status"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, requested_by=self.request.user)


class OTScheduleViewSet(OTScheduleWorkflowMixin, AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = OTScheduleSerializer
    queryset = OTSchedule.objects.all()
    filterset_fields = ["surgery_request", "surgeon", "operation_theatre_room"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        instance = serializer.save(hospital=hospital)
        instance.surgery_request.status = SurgeryRequest.Status.SCHEDULED
        instance.surgery_request.save(update_fields=["status"])

    @action(detail=False, methods=["get"])
    def board(self, request):
        """GET ?date=YYYY-MM-DD — the day's cases by room, each tagged OPD / IPD / emergency for the board colours."""
        try:
            day = date.fromisoformat(request.query_params["date"]) if request.query_params.get("date") else timezone.localdate()
        except ValueError:
            return Response({"date": "Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(day, time.min), tz)
        qs = (
            self.get_queryset()
            .filter(scheduled_start__lt=start + timedelta(days=1), scheduled_end__gt=start)
            .select_related("surgery_request__patient", "surgery_request__admission", "surgery_request__surgery_grade", "surgeon", "assistant_surgeon", "anaesthetist__doctor_profile")
            .order_by("operation_theatre_room", "scheduled_start")
        )
        cases, counts = [], {"opd": 0, "ipd": 0, "emergency": 0}
        for s in qs:
            req = s.surgery_request
            enc = req.encounter
            if s.status != OTSchedule.Status.CANCELLED:
                counts[enc] += 1
            a = s.anaesthetist
            cases.append({
                "id": s.pk, "surgery_request": req.pk, "room": s.operation_theatre_room, "status": s.status,
                "scheduled_start": s.scheduled_start, "scheduled_end": s.scheduled_end, "actual_start": s.actual_start, "actual_end": s.actual_end,
                "patient_name": req.patient.full_name, "patient_uhid": req.patient.uhid, "procedure": req.proposed_procedure,
                "encounter": enc, "encounter_overridden": bool(req.encounter_type), "priority": req.priority,
                "grade": req.surgery_grade.name if req.surgery_grade else "", "surgeon": f"Dr. {s.surgeon.name}",
                "assistant": f"Dr. {s.assistant_surgeon.name}" if s.assistant_surgeon else "",
                "anaesthetist": (f"Dr. {a.doctor_profile.name}" if getattr(a, "doctor_profile", None) else (a.get_full_name() or a.email)) if a else "",
                "cancellation_reason": s.cancellation_reason, "charges_posted_at": s.charges_posted_at,
            })
        rooms = sorted({c["room"] for c in cases})
        return Response({"date": day, "rooms": rooms, "cases": cases, "counts": counts})

    @action(detail=True, methods=["get", "post"])
    def charges(self, request, pk=None):
        """GET: what the OT tariff would charge for this surgery. POST: post it to the bill (idempotent)."""
        from . import charges

        schedule = self.get_object()
        if request.method == "GET":
            return Response(charges.describe(charges.compute_ot_charges(schedule)))
        if schedule.status == OTSchedule.Status.CANCELLED:
            return Response({"detail": "The surgery is cancelled."}, status=status.HTTP_400_BAD_REQUEST)
        bill, result = charges.post_ot_charges(schedule)
        return Response(charges.describe(result, bill))


class PreOpChecklistViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = PreOpChecklistSerializer
    queryset = PreOpChecklist.objects.all()
    filterset_fields = ["surgery_request"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, completed_by=self.request.user)


class OperativeNoteViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    action_permissions = {"finalize": "ot.finalize_operativenote"}
    audited_fields = ("procedure_performed", "findings")
    serializer_class = OperativeNoteSerializer
    queryset = OperativeNote.objects.all()
    filterset_fields = ["ot_schedule", "surgeon"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)

    @action(detail=True, methods=["post"])
    def finalize(self, request, pk=None):
        note = self.get_object()
        note.finalize(request.user)
        return Response(self.get_serializer(note).data)


class AnaesthesiaRecordViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    action_permissions = {"finalize": "ot.finalize_anaesthesiarecord"}
    audited_fields = ("anaesthesia_type", "intra_op_notes")
    serializer_class = AnaesthesiaRecordSerializer
    queryset = AnaesthesiaRecord.objects.all()
    filterset_fields = ["ot_schedule", "anaesthetist"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)

    @action(detail=True, methods=["post"])
    def finalize(self, request, pk=None):
        record = self.get_object()
        record.finalize(request.user)
        return Response(self.get_serializer(record).data)

    @action(detail=True, methods=["get"])
    def chart(self, request, pk=None):
        """The printable anaesthesia chart (PDF)."""
        from .anaesthesia_chart import render_anaesthesia_chart

        record = self.get_object()
        resp = HttpResponse(render_anaesthesia_chart(record), content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="anaesthesia-chart-{record.pk}.pdf"'
        return resp


class ConsumableUsageViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = ConsumableUsageSerializer
    queryset = ConsumableUsage.objects.all()
    filterset_fields = ["ot_schedule"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)


class ImplantUsageViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = ImplantUsageSerializer
    queryset = ImplantUsage.objects.all()
    filterset_fields = ["ot_schedule"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)
