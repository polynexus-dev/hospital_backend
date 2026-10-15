"""NABH COP.6 workflow endpoints layered onto the existing OT ViewSets:
schedule changes with reasons, actual timings, the WHO surgical safety
checklist, and the implant register."""
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.crud import ClinicalCRUDViewSet, model_serializer

from .models import ImplantUsage, OTSchedule, SurgicalSafetyChecklist


class OTScheduleWorkflowMixin:
    @action(detail=True, methods=["post"])
    def reschedule(self, request, pk=None):
        sch = self.get_object()
        reason = str(request.data.get("reason", "")).strip()
        if not reason or not request.data.get("scheduled_start") or not request.data.get("scheduled_end"):
            return Response({"detail": "scheduled_start, scheduled_end and reason are required."}, status=400)
        from django.utils.dateparse import parse_datetime

        sch.scheduled_start = parse_datetime(request.data["scheduled_start"])
        sch.scheduled_end = parse_datetime(request.data["scheduled_end"])
        if not sch.scheduled_start or not sch.scheduled_end or sch.scheduled_end <= sch.scheduled_start:
            return Response({"detail": "Invalid times."}, status=400)
        sch.reschedule_count += 1
        sch.last_reschedule_reason = reason[:255]
        sch.status = OTSchedule.Status.SCHEDULED
        sch.updated_by = request.user
        sch.save()
        return Response(self.get_serializer(sch).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        sch = self.get_object()
        reason = str(request.data.get("reason", "")).strip()
        if not reason:
            return Response({"reason": "A cancellation reason is required."}, status=400)
        if sch.status == OTSchedule.Status.COMPLETED:
            return Response({"detail": "A completed surgery can't be cancelled."}, status=400)
        from .charges import remove_ot_charges

        remove_ot_charges(sch)  # refuses (bill on hold) before anything changes
        sch.status = OTSchedule.Status.CANCELLED
        sch.cancelled_at = timezone.now()
        sch.cancellation_reason = reason[:255]
        sch.updated_by = sch.surgery_request.updated_by = request.user
        sch.save(update_fields=["status", "cancelled_at", "cancellation_reason", "updated_by", "updated_at"])
        sch.surgery_request.status = "cancelled"
        sch.surgery_request.save(update_fields=["status", "updated_by", "updated_at"])
        return Response(self.get_serializer(sch).data)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        sch = self.get_object()
        checklist = getattr(sch, "safety_checklist", None)
        if checklist is None or not checklist.time_out_at:
            return Response({"detail": "Surgical safety checklist Time-Out must be completed before incision."}, status=400)
        sch.actual_start = timezone.now()
        sch.status = OTSchedule.Status.IN_PROGRESS
        sch.updated_by = request.user
        sch.save(update_fields=["actual_start", "status", "updated_by", "updated_at"])
        checklist.save()  # recompute antibiotic timing against the real incision time
        return Response(self.get_serializer(sch).data)

    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        sch = self.get_object()
        if not sch.actual_start:
            return Response({"detail": "Surgery has not started."}, status=400)
        sch.actual_end = timezone.now()
        sch.status = OTSchedule.Status.COMPLETED
        sch.updated_by = sch.surgery_request.updated_by = request.user
        sch.save(update_fields=["actual_end", "status", "updated_by", "updated_at"])
        sch.surgery_request.status = "completed"
        sch.surgery_request.save(update_fields=["status", "updated_by", "updated_at"])
        posted = self._post_charges_quietly(sch)
        data = dict(self.get_serializer(sch).data)
        data["charges"] = posted
        return Response(data)

    def _post_charges_quietly(self, sch):
        """Bills the OT tariff when the surgery ends. A billing problem (no
        grade, no tariff, bill on hold) must never stop the clinical step, so
        it's reported back instead and charges can be posted later."""
        from rest_framework.exceptions import ValidationError

        from . import charges

        if sch.surgery_request.surgery_grade_id is None:
            return {"posted": False, "problem": "No surgery grade — OT charges not posted."}
        try:
            bill, result = charges.post_ot_charges(sch)
        except ValidationError as e:
            detail = e.detail.get("detail") if isinstance(e.detail, dict) else e.detail
            return {"posted": False, "problem": str(detail[0] if isinstance(detail, list) else detail)}
        sch.refresh_from_db()
        return {"posted": True, **charges.describe(result, bill)}


class SurgicalSafetyChecklistSerializer(model_serializer(
    SurgicalSafetyChecklist,
    read_only=("sign_in", "sign_in_by", "sign_in_at", "time_out", "time_out_by", "time_out_at", "sign_out", "sign_out_by", "sign_out_at", "antibiotic_given_within_60_min"),
    extra={"patient_name": serializers.CharField(source="patient.full_name", read_only=True)},
)):
    is_complete = serializers.BooleanField(read_only=True)
    updated_by_name = serializers.SerializerMethodField()

    def get_updated_by_name(self, obj):
        return (obj.updated_by.get_full_name() or obj.updated_by.email) if obj.updated_by else ""

    def save(self, **kwargs):
        request = self.context.get("request")
        if request is not None and getattr(request.user, "is_authenticated", False):
            kwargs.setdefault("updated_by", request.user)
        return super().save(**kwargs)
    items = serializers.SerializerMethodField()

    def get_items(self, obj):
        return SurgicalSafetyChecklist.PHASE_ITEMS


class SurgicalSafetyChecklistViewSet(ClinicalCRUDViewSet):
    serializer_class = SurgicalSafetyChecklistSerializer
    queryset = SurgicalSafetyChecklist.objects.select_related("patient", "ot_schedule")
    filterset_fields = ["patient", "ot_schedule", "setting"]
    audited_fields = ("procedure_name", "antibiotic_given_at")

    @action(detail=True, methods=["post"], url_path=r"phase/(?P<phase>sign_in|time_out|sign_out)")
    def complete_phase(self, request, pk=None, phase=None):
        """Phases must be completed in WHO order, every item must be ticked
        (or the phase can't be signed), and each is stamped by user+time."""
        c = self.get_object()
        order = ["sign_in", "time_out", "sign_out"]
        idx = order.index(phase)
        if idx and not getattr(c, f"{order[idx - 1]}_at"):
            return Response({"detail": f"Complete {order[idx - 1].replace('_', ' ')} first."}, status=400)
        answers = request.data.get("answers") or {}
        missing = [i for i in SurgicalSafetyChecklist.PHASE_ITEMS[phase] if not answers.get(i)]
        if missing:
            return Response({"detail": "All items must be confirmed.", "missing": missing}, status=400)
        setattr(c, phase, answers)
        setattr(c, f"{phase}_by", request.user)
        setattr(c, f"{phase}_at", timezone.now())
        if phase == "time_out" and request.data.get("antibiotic_given_at"):
            from django.utils.dateparse import parse_datetime

            c.antibiotic_given_at = parse_datetime(request.data["antibiotic_given_at"])
        c.updated_by = request.user
        c.save()
        return Response(self.get_serializer(c).data)


class ImplantRegisterViewSet(ClinicalCRUDViewSet):
    """MOM.3.c — read-only, searchable register across all surgeries (for
    recalls: 'which patients got batch X?')."""

    serializer_class = model_serializer(ImplantUsage, extra={
        "patient_id": serializers.IntegerField(source="ot_schedule.surgery_request.patient_id", read_only=True),
        "patient_name": serializers.CharField(source="ot_schedule.surgery_request.patient.full_name", read_only=True),
        "patient_uhid": serializers.CharField(source="ot_schedule.surgery_request.patient.uhid", read_only=True),
        "surgery_date": serializers.DateTimeField(source="ot_schedule.scheduled_start", read_only=True),
    })
    queryset = ImplantUsage.objects.select_related("ot_schedule__surgery_request__patient")
    filterset_fields = ["manufacturer", "batch_number"]
    search_fields = ["implant_name", "serial_number", "batch_number", "manufacturer"]
    http_method_names = ["get", "head", "options"]
