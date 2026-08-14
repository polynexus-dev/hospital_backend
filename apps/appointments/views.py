from datetime import datetime

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.viewsets import TenantScopedViewSetMixin
from apps.patients.models import Patient

from .models import Appointment, Doctor, Slot, SlotTemplate, Waitlist
from .serializers import (
    AppointmentSerializer,
    BlockDoctorSlotsSerializer,
    BookAppointmentSerializer,
    DoctorQueueSerializer,
    DoctorSerializer,
    GenerateSlotsSerializer,
    RescheduleAppointmentSerializer,
    SlotSerializer,
    SlotTemplateSerializer,
    WaitlistSerializer,
)
from .services import (
    SlotUnavailable,
    block_doctor_slots,
    book_appointment,
    cancel,
    check_in,
    complete,
    doctor_queue,
    generate_slots,
    mark_no_show,
    reschedule_appointment,
    start_consult,
    unblock_doctor_slots,
)


class DoctorViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = DoctorSerializer
    queryset = Doctor.objects.all()
    filterset_fields = ["department", "is_active"]
    search_fields = ["name", "speciality"]

    @action(detail=True, methods=["post"], url_path="block-leave")
    def block_leave(self, request, pk=None):
        doctor = self.get_object()
        serializer = BlockDoctorSlotsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = block_doctor_slots(doctor, **serializer.validated_data)
        return Response(result)

    @action(detail=True, methods=["post"], url_path="unblock-leave")
    def unblock_leave(self, request, pk=None):
        doctor = self.get_object()
        serializer = BlockDoctorSlotsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        unblocked = unblock_doctor_slots(
            doctor, start_date=serializer.validated_data["start_date"], end_date=serializer.validated_data["end_date"]
        )
        return Response({"unblocked": unblocked})

    @action(detail=True, methods=["get"])
    def queue(self, request, pk=None):
        doctor = self.get_object()
        date_param = request.query_params.get("date")
        date = datetime.strptime(date_param, "%Y-%m-%d").date() if date_param else timezone.localdate()
        return Response(DoctorQueueSerializer(doctor_queue(doctor, date)).data)


class SlotTemplateViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = SlotTemplateSerializer
    queryset = SlotTemplate.objects.all()
    filterset_fields = ["doctor", "weekday", "is_active"]

    @action(detail=True, methods=["post"], url_path="generate-slots")
    def generate_slots_action(self, request, pk=None):
        template = self.get_object()
        serializer = GenerateSlotsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        created = generate_slots(template, weeks_ahead=serializer.validated_data["weeks_ahead"])
        return Response({"slots_created": created})


class SlotViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = SlotSerializer
    queryset = Slot.objects.select_related("appointment").all()
    filterset_fields = ["doctor", "date", "is_blocked"]

    @action(detail=False, methods=["get"])
    def available(self, request):
        qs = self.filter_queryset(self.get_queryset()).filter(is_blocked=False, appointment__isnull=True)
        return Response(SlotSerializer(qs, many=True).data)


class AppointmentViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = AppointmentSerializer
    queryset = Appointment.objects.select_related("patient", "doctor", "slot").all()
    filterset_fields = ["status", "doctor", "patient", "source", "slot__date"]

    def create(self, request, *args, **kwargs):
        serializer = BookAppointmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        patient = get_object_or_404(Patient, pk=data["patient"])
        slot = get_object_or_404(Slot, pk=data["slot"])
        try:
            appointment = book_appointment(
                patient=patient, slot=slot, source=data["source"], reason=data["reason"], booked_by=request.user
            )
        except SlotUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(AppointmentSerializer(appointment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="check-in")
    def check_in_action(self, request, pk=None):
        return Response(AppointmentSerializer(check_in(self.get_object())).data)

    @action(detail=True, methods=["post"], url_path="start-consult")
    def start_consult_action(self, request, pk=None):
        return Response(AppointmentSerializer(start_consult(self.get_object())).data)

    @action(detail=True, methods=["post"])
    def complete_action(self, request, pk=None):
        return Response(AppointmentSerializer(complete(self.get_object())).data)

    @action(detail=True, methods=["post"])
    def cancel_action(self, request, pk=None):
        return Response(AppointmentSerializer(cancel(self.get_object())).data)

    @action(detail=True, methods=["post"], url_path="no-show")
    def no_show_action(self, request, pk=None):
        return Response(AppointmentSerializer(mark_no_show(self.get_object())).data)

    @action(detail=True, methods=["post"])
    def reschedule(self, request, pk=None):
        serializer = RescheduleAppointmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_slot = get_object_or_404(Slot, pk=serializer.validated_data["new_slot"])
        try:
            new_appointment = reschedule_appointment(self.get_object(), new_slot=new_slot, changed_by=request.user)
        except SlotUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(AppointmentSerializer(new_appointment).data, status=status.HTTP_201_CREATED)


class WaitlistViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = WaitlistSerializer
    queryset = Waitlist.objects.select_related("patient", "doctor", "department", "offered_slot").all()
    filterset_fields = ["doctor", "department", "status"]

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        entry = self.get_object()
        if entry.offered_slot is None:
            return Response({"detail": "This waitlist entry has no offered slot to confirm."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            appointment = book_appointment(patient=entry.patient, slot=entry.offered_slot, booked_by=request.user)
        except SlotUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        entry.status = Waitlist.Status.BOOKED
        entry.resulting_appointment = appointment
        entry.save(update_fields=["status", "resulting_appointment"])
        return Response(WaitlistSerializer(entry).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        entry = self.get_object()
        entry.status = Waitlist.Status.CANCELLED
        entry.save(update_fields=["status"])
        return Response(WaitlistSerializer(entry).data)


class PaperlessRegistrationView(APIView):
    """Public endpoint behind the WhatsApp / front-desk-tablet registration
    link — no login, just the unguessable token (§4)."""

    permission_classes = [AllowAny]
    serializer_class = AppointmentSerializer

    def get(self, request, token):
        appointment = get_object_or_404(Appointment, registration_token=token)
        return Response(AppointmentSerializer(appointment).data)

    def post(self, request, token):
        appointment = get_object_or_404(Appointment, registration_token=token)
        appointment.consent_captured = True
        appointment.consent_captured_at = timezone.now()
        appointment.save(update_fields=["consent_captured", "consent_captured_at"])
        return Response(AppointmentSerializer(appointment).data)
