from rest_framework import serializers
from .models import AuditLog, Hospital


class AuditLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.SerializerMethodField()
    object_repr = serializers.SerializerMethodField()
    changes = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            "id", "actor", "actor_email", "action", "model_name", "object_id",
            "object_repr", "changes", "method", "path", "status_code",
            "ip_address", "created_at",
        ]
        read_only_fields = fields

    def get_actor_email(self, obj) -> str | None:
        return obj.actor.email if obj.actor_id else None

    def get_object_repr(self, obj) -> str:
        request = self.context.get("request")
        if request and (getattr(request.user, "email", "") == "saas_owner@hospital-crm.com" or getattr(request.user, "hospital_id", None) is None):
            if obj.model_name in ["Patient", "EDVisit", "ICUAdmission", "Prescription", "BillItem", "Transfusion"]:
                return f"{obj.model_name} #{obj.object_id} [REDACTED_PATIENT_PII]"
        return obj.object_repr

    def get_changes(self, obj) -> dict | list | None:
        request = self.context.get("request")
        if request and (getattr(request.user, "email", "") == "saas_owner@hospital-crm.com" or getattr(request.user, "hospital_id", None) is None):
            if obj.model_name in ["Patient", "EDVisit", "ICUAdmission", "Prescription", "BillItem", "Transfusion"]:
                return {"note": "[REDACTED_PATIENT_PII_UNDER_DPDP_ACT_2023]"}
        return obj.changes


class HospitalSerializer(serializers.ModelSerializer):
    patient_count = serializers.SerializerMethodField()
    appointment_count = serializers.SerializerMethodField()
    total_revenue = serializers.SerializerMethodField()

    class Meta:
        model = Hospital
        fields = [
            "id", "name", "slug", "city", "state", "address",
            "timezone", "primary_language", "is_on_premise", "is_active",
            "enabled_modules", "patient_count", "appointment_count", "total_revenue",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {"slug": {"required": False}}

    def create(self, validated_data):
        if not validated_data.get("slug"):
            from django.utils.text import slugify
            validated_data["slug"] = slugify(validated_data["name"])[:20].rstrip("-")
        return super().create(validated_data)

    def get_patient_count(self, obj) -> int:
        from apps.patients.models import Patient
        return Patient.objects.filter(hospital=obj).count()

    def get_appointment_count(self, obj) -> int:
        from apps.appointments.models import Appointment
        return Appointment.objects.filter(hospital=obj).count()

    def get_total_revenue(self, obj) -> float:
        from apps.billing.models import Bill
        from django.db.models import Sum
        val = Bill.objects.filter(hospital=obj).aggregate(total=Sum("net_amount"))["total"]
        return float(val) if val else 0.0
