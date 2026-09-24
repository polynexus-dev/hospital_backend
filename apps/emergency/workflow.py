"""NABH COP.4.b — medico-legal case labelling."""
from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import EDVisit


class EDVisitWorkflowMixin:
    @action(detail=True, methods=["post"])
    def mark_mlc(self, request, pk=None):
        visit = self.get_object()
        if visit.is_mlc:
            return Response({"detail": f"Already MLC {visit.mlc_number}."}, status=400)
        mlc_type = str(request.data.get("mlc_type", "")).strip()
        police_station = str(request.data.get("police_station", "")).strip()
        checklist = request.data.get("checklist") or {}
        missing = [f for f, v in (("mlc_type", mlc_type), ("police_station", police_station)) if not v]
        if missing:
            return Response({"detail": "Required for an MLC: " + ", ".join(missing)}, status=400)
        if not checklist.get("police_intimation_sent"):
            return Response({"checklist": "Police intimation must be sent (and recorded) when a case is marked MLC."}, status=400)
        with transaction.atomic():
            year = timezone.localdate().year
            n = EDVisit.objects.select_for_update().filter(hospital_id=visit.hospital_id, is_mlc=True, mlc_marked_at__year=year).count() + 1
            visit.is_mlc = True
            visit.mlc_number = f"MLC/{year}/{n:05d}"
            visit.mlc_type = mlc_type[:40]
            visit.police_station = police_station[:150]
            visit.police_officer_name = str(request.data.get("police_officer_name", ""))[:150]
            visit.police_intimated_at = timezone.now()
            visit.injuries_description = str(request.data.get("injuries_description", visit.injuries_description))
            visit.mlc_checklist = {k: bool(checklist.get(k)) for k in EDVisit.MLC_CHECKLIST_ITEMS}
            visit.mlc_marked_by = request.user
            visit.mlc_marked_at = timezone.now()
            visit.save()
        return Response(self.get_serializer(visit).data)

    @action(detail=False, methods=["get"])
    def mlc_register(self, request):
        qs = self.get_queryset().filter(is_mlc=True).order_by("-mlc_marked_at")
        return Response(self.get_serializer(qs, many=True).data)
