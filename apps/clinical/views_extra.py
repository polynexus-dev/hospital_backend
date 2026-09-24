"""Specialty referrals (AAC.1.k), affiliate record sharing (AAC.1.j) and
medical-device vitals ingestion (AAC.1.l, COP.5.c)."""
import secrets

from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.crud import ClinicalCRUDViewSet, TenantCRUDViewSet, model_serializer
from apps.core.permissions import RequiresClinicalDetailPermission

from . import scoring
from .models import ClinicalAlert, DeviceVitalSign, MedicalDevice, RecordShare, SpecialtyReferral

_patient = {
    "patient_name": serializers.CharField(source="patient.full_name", read_only=True),
    "patient_uhid": serializers.CharField(source="patient.uhid", read_only=True),
}


class SpecialtyReferralViewSet(ClinicalCRUDViewSet):
    serializer_class = model_serializer(SpecialtyReferral, read_only=("from_doctor", "status", "response", "responded_at"), extra={
        **_patient,
        "to_doctor_name": serializers.CharField(source="to_doctor.name", read_only=True, default=None),
        "to_department_name": serializers.CharField(source="to_department.name", read_only=True, default=None),
    })
    queryset = SpecialtyReferral.objects.select_related("patient", "to_doctor", "to_department")
    filterset_fields = ["patient", "status", "urgency", "to_doctor", "to_department"]
    actor_field = "from_doctor"

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("incoming") == "1":
            qs = qs.filter(to_doctor__user=self.request.user)
        return qs

    def perform_create(self, serializer):
        super().perform_create(serializer)
        r = serializer.instance
        target = r.to_doctor.user if r.to_doctor and r.to_doctor.user_id else None
        ClinicalAlert.objects.create(
            hospital_id=r.hospital_id, patient=r.patient, alert_type=ClinicalAlert.AlertType.CDSS,
            severity=ClinicalAlert.Severity.CRITICAL if r.urgency == "emergency" else ClinicalAlert.Severity.INFO,
            target_user=target, target_department=(r.to_department.name if r.to_department and not target else ""),
            title=f"{r.get_urgency_display()} referral: {r.patient.full_name}", message=r.reason[:500], object_id=str(r.pk),
        )

    @action(detail=True, methods=["post"])
    def respond(self, request, pk=None):
        r = self.get_object()
        new = request.data.get("status")
        if new not in (SpecialtyReferral.Status.ACCEPTED, SpecialtyReferral.Status.SEEN, SpecialtyReferral.Status.DECLINED):
            return Response({"status": "accepted | seen | declined"}, status=400)
        r.status = new
        r.response = str(request.data.get("response", r.response))
        r.responded_at = timezone.now()
        r.save()
        return Response(self.get_serializer(r).data)


class RecordShareViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(RecordShare, read_only=("shared_by", "revoked_at"), extra={
        **_patient, "shared_with_name": serializers.CharField(source="shared_with.name", read_only=True),
    })
    queryset = RecordShare.objects.select_related("patient", "shared_with")
    filterset_fields = ["patient", "shared_with"]
    actor_field = "shared_by"

    def perform_create(self, serializer):
        from rest_framework.exceptions import ValidationError

        target = serializer.validated_data["shared_with"]
        me = self.request.user.hospital
        if target.pk == me.pk or not me.group_id or target.group_id != me.group_id:
            raise ValidationError({"shared_with": "Records can only be shared with another facility in your hospital group."})
        if not serializer.validated_data.get("patient_consent"):
            raise ValidationError({"patient_consent": "Patient consent is required to share records."})
        super().perform_create(serializer)

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        s = self.get_object()
        s.revoked_at = timezone.now()
        s.save(update_fields=["revoked_at"])
        return Response(self.get_serializer(s).data)


class SharedWithMeView(APIView):
    """Receiving facility: list active shares and open the clinical summary."""

    permission_classes = [IsAuthenticated, RequiresClinicalDetailPermission]

    def get(self, request, share_id=None):
        active = RecordShare.objects.filter(shared_with_id=request.user.hospital_id, revoked_at__isnull=True, expires_at__gt=timezone.now()).select_related("patient", "hospital")
        if share_id is None:
            return Response([{"id": s.pk, "patient": s.patient.full_name, "uhid": s.patient.uhid, "from": s.hospital.name, "purpose": s.purpose, "expires_at": s.expires_at} for s in active])
        share = active.filter(pk=share_id).first()
        if share is None:
            return Response({"detail": "Not shared with you or expired."}, status=404)
        from apps.core.audit import log_action

        from .views import PatientClinicalSummaryView

        log_action(actor=request.user, action="read", instance=share.patient, changes={"via_share": share.pk}, request=request, force=True)
        view = PatientClinicalSummaryView()
        return view.build(share.patient)


class MedicalDeviceViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(MedicalDevice, read_only=("api_token", "last_seen_at"))
    queryset = MedicalDevice.objects.select_related("bed")
    filterset_fields = ["kind", "is_active", "bed"]

    @action(detail=True, methods=["post"])
    def rotate_token(self, request, pk=None):
        d = self.get_object()
        d.api_token = secrets.token_hex(24)
        d.save(update_fields=["api_token"])
        return Response({"api_token": d.api_token})


class DeviceVitalSignViewSet(ClinicalCRUDViewSet):
    serializer_class = model_serializer(DeviceVitalSign, read_only=("news2_score",))
    queryset = DeviceVitalSign.objects.all()
    filterset_fields = ["patient", "admission", "device"]
    http_method_names = ["get", "head", "options"]


class DeviceVitalsIngestView(APIView):
    """POST with X-Device-Token. The reading is attached to whoever is in
    the device's bed right now; NEWS2 is computed and a high score alerts
    the nurses (COP.12.b)."""

    permission_classes = [AllowAny]
    authentication_classes = []
    FIELDS = ("heart_rate", "spo2", "respiratory_rate", "bp_systolic", "bp_diastolic", "temperature_c", "etco2")

    def post(self, request):
        token = request.headers.get("X-Device-Token", "")
        device = MedicalDevice.objects.filter(api_token=token, is_active=True).exclude(api_token="").select_related("bed").first() if token else None
        if device is None:
            return Response({"detail": "Invalid device token."}, status=401)
        admission = device.bed.current_admission if device.bed_id else None
        if admission is None:
            return Response({"detail": "No patient currently assigned to this device's bed."}, status=409)
        values = {k: request.data.get(k) for k in self.FIELDS if request.data.get(k) not in (None, "")}
        score, level = scoring.news2({
            "respiratory_rate": values.get("respiratory_rate", 16), "spo2": values.get("spo2", 98), "temperature_c": values.get("temperature_c", 37),
            "systolic_bp": values.get("bp_systolic", 120), "heart_rate": values.get("heart_rate", 80),
        })
        reading = DeviceVitalSign.objects.create(hospital_id=device.hospital_id, device=device, patient=admission.patient, admission=admission, news2_score=score, **values)
        device.last_seen_at = timezone.now()
        device.save(update_fields=["last_seen_at"])
        if level == "high":
            recent = ClinicalAlert.objects.filter(patient=admission.patient, alert_type=ClinicalAlert.AlertType.EARLY_WARNING, acknowledged_at__isnull=True,
                                                  created_at__gte=timezone.now() - timezone.timedelta(minutes=30)).exists()
            if not recent:
                ClinicalAlert.objects.create(
                    hospital_id=device.hospital_id, patient=admission.patient, alert_type=ClinicalAlert.AlertType.EARLY_WARNING,
                    severity=ClinicalAlert.Severity.CRITICAL, target_department="nursing",
                    title=f"NEWS2 {score} from {device.name} — bed {device.bed.bed_number}",
                    message="Automated monitor reading indicates high clinical risk. Assess immediately and escalate to the rapid response team.",
                    object_id=str(reading.pk),
                )
        return Response({"id": reading.pk, "news2": score, "risk": level})
