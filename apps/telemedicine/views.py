from django.core import signing
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.crud import ClinicalCRUDViewSet, model_serializer
from apps.core.modules import require_module

from .models import TeleConsultation

JOIN_SALT = "telemedicine.join"


def patient_join_token(consult):
    return signing.dumps({"c": consult.pk, "r": consult.room_name}, salt=JOIN_SALT)


def read_join_token(token, max_age=60 * 60 * 24 * 7):
    data = signing.loads(token, salt=JOIN_SALT, max_age=max_age)
    return TeleConsultation.objects.filter(pk=data["c"], room_name=data["r"]).select_related("patient", "doctor", "hospital").first()


class TeleConsultationSerializer(model_serializer(TeleConsultation, read_only=(
    "room_name", "status", "consent_at", "patient_joined_at", "doctor_joined_at", "ended_at", "link_sent_at", "created_by",
), extra={
    "patient_name": serializers.CharField(source="patient.full_name", read_only=True),
    "patient_uhid": serializers.CharField(source="patient.uhid", read_only=True),
    "doctor_name": serializers.CharField(source="doctor.name", read_only=True),
})):
    video_url = serializers.CharField(read_only=True)
    wait_minutes = serializers.FloatField(read_only=True)
    patient_join_link = serializers.SerializerMethodField()

    def get_patient_join_link(self, obj):
        return f"/tele/join/{patient_join_token(obj)}"


class TeleConsultationViewSet(ClinicalCRUDViewSet):
    serializer_class = TeleConsultationSerializer
    queryset = TeleConsultation.objects.select_related("patient", "doctor")
    filterset_fields = ["patient", "doctor", "status", "mode"]
    actor_field = "created_by"
    audited_fields = ("status", "scheduled_at", "clinical_notes")

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("today") == "1":
            qs = qs.filter(scheduled_at__date=timezone.localdate())
        if self.request.query_params.get("mine") == "1":
            qs = qs.filter(doctor__user=self.request.user)
        return qs

    @action(detail=True, methods=["post"])
    def send_link(self, request, pk=None):
        from apps.clinical.notify import notify_patient

        c = self.get_object()
        base = request.data.get("portal_base_url") or request.build_absolute_uri("/").rstrip("/")
        notify_patient(c.patient, "teleconsult_link", {
            "doctor_name": c.doctor.name, "time": timezone.localtime(c.scheduled_at).strftime("%d %b %Y %I:%M %p"),
            "link": f"{base}/tele/join/{patient_join_token(c)}",
        }, sent_by=request.user)
        c.link_sent_at = timezone.now()
        c.save(update_fields=["link_sent_at"])
        return Response(self.get_serializer(c).data)

    @action(detail=True, methods=["post"])
    def doctor_join(self, request, pk=None):
        c = self.get_object()
        if c.status in (TeleConsultation.Status.COMPLETED, TeleConsultation.Status.CANCELLED):
            return Response({"detail": f"Consultation is {c.status}."}, status=400)
        c.doctor_joined_at = c.doctor_joined_at or timezone.now()
        c.status = TeleConsultation.Status.IN_PROGRESS
        c.save(update_fields=["doctor_joined_at", "status"])
        return Response({**self.get_serializer(c).data, "join_url": c.video_url})

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        c = self.get_object()
        c.status = TeleConsultation.Status.COMPLETED
        c.ended_at = timezone.now()
        c.clinical_notes = request.data.get("clinical_notes", c.clinical_notes)
        if request.data.get("prescription"):
            c.prescription_id = request.data["prescription"]
        c.save()
        if c.appointment_id:
            from apps.appointments.services import complete as complete_appointment

            if c.appointment.status != "completed":
                complete_appointment(c.appointment)
        return Response(self.get_serializer(c).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        c = self.get_object()
        c.status = TeleConsultation.Status.CANCELLED
        c.save(update_fields=["status"])
        return Response(self.get_serializer(c).data)


class PatientJoinView(APIView):
    """Public: the patient opens the signed link, gives teleconsultation
    consent, and gets the room URL. No account/login required."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def _consult(self, token):
        try:
            return read_join_token(token)
        except signing.BadSignature:
            return None

    def get(self, request, token):
        c = self._consult(token)
        if c is None:
            return Response({"detail": "This link is invalid or has expired."}, status=404)
        require_module(c.hospital, "telemedicine")
        return Response({
            "hospital": c.hospital.name, "doctor": c.doctor.name, "patient_first_name": c.patient.first_name,
            "scheduled_at": c.scheduled_at, "status": c.status, "consent_given": c.consent_given, "mode": c.mode,
        })

    def post(self, request, token):
        c = self._consult(token)
        if c is None:
            return Response({"detail": "This link is invalid or has expired."}, status=404)
        require_module(c.hospital, "telemedicine")
        if c.status in (TeleConsultation.Status.COMPLETED, TeleConsultation.Status.CANCELLED):
            return Response({"detail": f"This consultation is {c.get_status_display().lower()}."}, status=400)
        if not request.data.get("consent"):
            return Response({"consent": "Please accept the teleconsultation consent to continue."}, status=400)
        if not c.consent_given:
            c.consent_given = True
            c.consent_at = timezone.now()
            from apps.clinical.models import ConsentRecord

            ConsentRecord.objects.create(hospital_id=c.hospital_id, patient=c.patient, consent_type=ConsentRecord.ConsentType.TELEMEDICINE,
                                         procedure_name=f"Teleconsultation with {c.doctor.name}", status=ConsentRecord.Status.GRANTED)
        c.patient_joined_at = c.patient_joined_at or timezone.now()
        if c.status == TeleConsultation.Status.SCHEDULED:
            c.status = TeleConsultation.Status.WAITING
        c.save()
        return Response({"join_url": c.video_url, "status": c.status})
