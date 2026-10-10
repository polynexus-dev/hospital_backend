from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.shortcuts import get_object_or_404

from apps.core.viewsets import TenantScopedViewSetMixin
from apps.patients.models import Patient
from apps.appointments.models import Doctor
from apps.ipd.services import admit_patient, BedUnavailable
from apps.ipd.serializers import AdmissionSerializer
from apps.ipd.workflow import admission_created

from .models import Bed, Room, Ward
from .serializers import BedSerializer, RoomSerializer, WardSerializer


class WardViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = WardSerializer
    queryset = Ward.objects.all()
    filterset_fields = ["ward_type", "department", "is_active"]
    search_fields = ["name", "floor"]


class RoomViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = RoomSerializer
    queryset = Room.objects.all()
    filterset_fields = ["ward", "room_type", "is_active"]
    search_fields = ["room_number"]


class BedViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = BedSerializer
    queryset = Bed.objects.select_related("current_admission__patient", "reserved_for").all()
    filterset_fields = ["room", "room__ward", "bed_type", "status"]
    search_fields = ["bed_number"]

    def perform_create(self, serializer):
        from rest_framework.exceptions import ValidationError

        from apps.licensing.service import CapacityExceeded, check_capacity

        try:
            check_capacity("beds", Bed.objects.filter(hospital_id=self.request.user.hospital_id).count())
        except CapacityExceeded as exc:
            raise ValidationError({"detail": str(exc)})
        super().perform_create(serializer)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        bed = self.get_object()
        hospital = getattr(request.user, "hospital", None)
        
        patient_id = request.data.get("patient")
        doctor_id = request.data.get("admitting_doctor")
        
        if not patient_id or not doctor_id:
            return Response(
                {"detail": "Both 'patient' and 'admitting_doctor' are required."},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        patient = get_object_or_404(Patient, pk=patient_id, hospital=hospital)
        admitting_doctor = get_object_or_404(Doctor, pk=doctor_id, hospital=hospital)
        
        try:
            admission = admit_patient(
                hospital=hospital,
                patient=patient,
                admitting_doctor=admitting_doctor,
                bed=bed,
                admission_type=request.data.get("admission_type", "planned"),
                admission_diagnosis=request.data.get("admission_diagnosis", "")
            )
        except BedUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
            
        # Clear reserved state if this bed is being admitted
        if bed.status == Bed.Status.RESERVED:
            bed.reserved_for = None
            bed.save(update_fields=["reserved_for"])

        admission_created(admission)
        return Response(AdmissionSerializer(admission).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def types(self, request):
        """Returns the available bed types."""
        return Response([{"id": choice[0], "label": choice[1]} for choice in Bed.BedType.choices])

    @action(detail=True, methods=["post"])
    def update_status(self, request, pk=None):
        """Manually update bed status (e.g., from cleaning/maintenance to available)."""
        bed = self.get_object()
        new_status = request.data.get("status")

        if new_status not in dict(Bed.Status.choices):
            return Response(
                {"detail": f"Invalid status. Must be one of: {', '.join(dict(Bed.Status.choices).keys())}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if new_status == Bed.Status.RESERVED:
            patient_id = request.data.get("patient")
            if not patient_id:
                return Response(
                    {"detail": "Must provide 'patient' ID when reserving a bed."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            patient = get_object_or_404(Patient, pk=patient_id, hospital=getattr(request.user, "hospital", None))
            bed.reserved_for = patient
        else:
            bed.reserved_for = None

        if new_status == Bed.Status.AVAILABLE:
            bed.current_admission = None

        bed.status = new_status
        bed.save(update_fields=["status", "current_admission", "reserved_for"])
        return Response(self.get_serializer(bed).data)
