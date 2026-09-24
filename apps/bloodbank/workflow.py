"""NABH COP.3 workflow additions for the blood bank."""
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import Hospital

from .models import BloodUnit, CrossMatchRequest

STEP_FIELDS = {"sample_received": "sample_received_at", "grouping": "grouping_done_at", "crossmatch": "crossmatched_at", "issue": "issued_at"}


class CrossMatchWorkflowMixin:
    @action(detail=True, methods=["post"], url_path=r"step/(?P<step>sample_received|grouping|crossmatch|issue)")
    def step(self, request, pk=None, step=None):
        """Stamps a TAT sub-activity. `issue` also reserves→issues the unit
        and requires a delay reason when the TAT target was missed."""
        req = self.get_object()
        field = STEP_FIELDS[step]
        now = timezone.now()
        if step == "crossmatch":
            unit_id = request.data.get("unit")
            unit = BloodUnit.objects.filter(pk=unit_id, hospital_id=req.hospital_id, status=BloodUnit.Status.AVAILABLE).first() if unit_id else None
            if unit_id and unit is None:
                return Response({"unit": "Unit not available."}, status=400)
            if unit:
                if unit.expiry_date < timezone.localdate():
                    return Response({"unit": "Unit is expired."}, status=400)
                unit.status = BloodUnit.Status.RESERVED
                unit.save(update_fields=["status"])
                req.reserved_unit = unit
            req.status = CrossMatchRequest.Status.MATCHED
        if step == "issue":
            target = CrossMatchRequest.TAT_TARGET_MINUTES.get(req.urgency, 120)
            if (now - req.created_at) > timedelta(minutes=target) and not (request.data.get("delay_reason") or req.delay_reason):
                return Response({"delay_reason": f"TAT target of {target} min exceeded — a delay reason is required."}, status=400)
            if request.data.get("delay_reason"):
                req.delay_reason = str(request.data["delay_reason"])[:255]
        setattr(req, field, now)
        req.save()
        return Response(self.get_serializer(req).data)

    @action(detail=False, methods=["get"])
    def tat(self, request):
        since = timezone.now() - timedelta(days=int(request.query_params.get("days", 30)))
        qs = self.get_queryset().filter(created_at__gte=since, issued_at__isnull=False)
        mins = [r.turnaround_minutes for r in qs]
        return Response({
            "issued": len(mins),
            "average_minutes": round(sum(mins) / len(mins), 1) if mins else None,
            "delayed": [{"id": r.pk, "minutes": r.turnaround_minutes, "reason": r.delay_reason} for r in qs if r.delay_reason],
        })


class TransfusionWorkflowMixin:
    @action(detail=True, methods=["post"])
    def report_reaction(self, request, pk=None):
        """COP.3.d — records the reaction on the transfusion and opens a
        haemovigilance safety incident."""
        from apps.quality.models import SafetyIncident

        t = self.get_object()
        t.had_reaction = True
        t.reaction_type = str(request.data.get("reaction_type", "other"))[:40]
        t.reaction_severity = str(request.data.get("severity", ""))[:10]
        t.reaction_notes = str(request.data.get("notes", t.reaction_notes))
        t.ended_at = t.ended_at or timezone.now()
        t.save()
        harm = {"severe": "severe", "moderate": "moderate", "mild": "mild"}.get(t.reaction_severity, "mild")
        SafetyIncident.objects.create(
            hospital_id=t.hospital_id, incident_type=SafetyIncident.IncidentType.TRANSFUSION_REACTION, harm=harm,
            patient=t.patient, admission=t.admission, reported_by=request.user,
            description=f"{t.reaction_type} transfusion reaction — unit {t.blood_unit_id}. {t.reaction_notes}".strip(),
            immediate_action="Transfusion stopped; unit and post-transfusion sample returned to blood bank.",
        )
        return Response(self.get_serializer(t).data)


def stock_summary(hospital_id):
    today = timezone.localdate()
    rows = (
        BloodUnit.objects.filter(hospital_id=hospital_id, status=BloodUnit.Status.AVAILABLE, expiry_date__gte=today)
        .values("blood_group", "component").annotate(units=Count("id")).order_by("blood_group", "component")
    )
    return list(rows)


class PublicBloodStockView(APIView):
    """COP.3.e — real-time availability by group/component, shareable with
    external platforms (UHI / e-RaktKosh style) without exposing any donor
    or patient data. ?subdomain=<hospital slug>."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        hospital = Hospital.objects.filter(slug=(request.GET.get("subdomain") or "").lower(), is_active=True).first()
        if hospital is None:
            return Response({"detail": "Unknown hospital."}, status=404)
        return Response({"hospital": hospital.name, "city": hospital.city, "as_of": timezone.now(), "stock": stock_summary(hospital.pk)})
