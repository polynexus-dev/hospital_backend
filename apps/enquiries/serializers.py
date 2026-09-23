from rest_framework import serializers

from apps.accounts.models import User

from .models import Enquiry, EnquiryAssignmentChange, EnquiryStageChange, TreatmentEstimate


class EnquirySerializer(serializers.ModelSerializer):
    consulting_doctor_name = serializers.SerializerMethodField()

    class Meta:
        model = Enquiry
        fields = [
            "id", "patient", "name", "mobile", "alternate_mobile", "email",
            "source", "campaign",
            "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "landing_page", "referrer_url",
            "department", "consulting_doctor", "consulting_doctor_name",
            "service_requested", "urgency", "score",
            "stage", "assigned_to", "duplicate_of",
            "sla_due_at", "follow_up_date", "escalation_level", "lost_reason", "lost_notes", "notes",
            "estimated_value",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "assigned_to", "duplicate_of", "sla_due_at", "escalation_level", "created_at", "updated_at"]

    def get_consulting_doctor_name(self, obj) -> str:
        if not obj.consulting_doctor:
            return ""
        doc = obj.consulting_doctor
        return getattr(doc, "name", "Doctor")


class EnquiryStageChangeSerializer(serializers.ModelSerializer):
    changed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = EnquiryStageChange
        fields = ["id", "enquiry", "from_stage", "to_stage", "changed_by", "changed_by_name", "created_at"]
        read_only_fields = fields

    def get_changed_by_name(self, obj) -> str:
        if not obj.changed_by:
            return "System"
        return obj.changed_by.get_full_name() or obj.changed_by.email


class EnquiryAssignmentChangeSerializer(serializers.ModelSerializer):
    changed_by_name = serializers.SerializerMethodField()
    from_owner_name = serializers.SerializerMethodField()
    to_owner_name = serializers.SerializerMethodField()

    class Meta:
        model = EnquiryAssignmentChange
        fields = [
            "id", "enquiry",
            "from_owner", "from_owner_name",
            "to_owner", "to_owner_name",
            "changed_by", "changed_by_name",
            "reason", "created_at",
        ]
        read_only_fields = fields

    def get_changed_by_name(self, obj) -> str:
        if not obj.changed_by:
            return "System"
        return obj.changed_by.get_full_name() or obj.changed_by.email

    def get_from_owner_name(self, obj) -> str:
        if not obj.from_owner:
            return "Unassigned"
        return obj.from_owner.get_full_name() or obj.from_owner.email

    def get_to_owner_name(self, obj) -> str:
        if not obj.to_owner:
            return "Unassigned"
        return obj.to_owner.get_full_name() or obj.to_owner.email


class MoveStageSerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=Enquiry.Stage.choices)


class LoseEnquirySerializer(serializers.Serializer):
    lost_reason = serializers.ChoiceField(choices=Enquiry.LostReason.choices)
    lost_notes = serializers.CharField(required=False, allow_blank=True, default="")


class ReassignEnquirySerializer(serializers.Serializer):
    owner = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    reason = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_owner(self, owner):
        request = self.context.get("request")
        if request is not None and owner.hospital_id != request.user.hospital_id:
            raise serializers.ValidationError("Owner must belong to the same hospital as the enquiry.")
        return owner


class MergeEnquirySerializer(serializers.Serializer):
    primary_id = serializers.PrimaryKeyRelatedField(queryset=Enquiry.objects.all())


class BulkImportRowSerializer(serializers.Serializer):
    name = serializers.CharField()
    mobile = serializers.CharField()
    email = serializers.EmailField(required=False, allow_blank=True)
    source = serializers.ChoiceField(choices=Enquiry.Source.choices, default=Enquiry.Source.OTHER)
    service_requested = serializers.CharField(required=False, allow_blank=True, default="")


class LeadWebhookSerializer(serializers.Serializer):
    """Inbound payload from website forms / Meta & Google lead ads (§2).
    Deliberately permissive — third-party form builders vary in what they
    send, so only name+mobile are required."""

    name = serializers.CharField(max_length=255)
    mobile = serializers.CharField(max_length=20)
    alternate_mobile = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    source = serializers.ChoiceField(choices=Enquiry.Source.choices, default=Enquiry.Source.WEBSITE)
    campaign = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    service_requested = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    utm_source = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    utm_medium = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    utm_campaign = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    utm_term = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    utm_content = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    landing_page = serializers.URLField(max_length=500, required=False, allow_blank=True, default="")
    referrer_url = serializers.URLField(max_length=500, required=False, allow_blank=True, default="")


class TreatmentEstimateSerializer(serializers.ModelSerializer):
    patient_name = serializers.SerializerMethodField()
    patient_mobile = serializers.SerializerMethodField()
    doctor_name = serializers.SerializerMethodField()
    department_name = serializers.SerializerMethodField()

    class Meta:
        model = TreatmentEstimate
        fields = [
            "id", "patient", "patient_name", "patient_mobile",
            "enquiry", "doctor", "doctor_name", "department", "department_name",
            "procedure_name", "diagnosis", "room_category", "stay_days",
            "surgeon_fee", "ot_charges", "room_charges", "medicines_estimate", "implants_investigations",
            "total_estimate", "payment_mode", "tpa_name", "insurance_preauth_status", "approved_preauth_amount",
            "stage", "drop_reason", "notes", "valid_until", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_patient_name(self, obj) -> str:
        if obj.patient:
            return obj.patient.full_name
        if obj.enquiry:
            return obj.enquiry.name
        return "Unknown Patient"

    def get_patient_mobile(self, obj) -> str:
        if obj.patient:
            return getattr(obj.patient, "mobile", "")
        if obj.enquiry:
            return getattr(obj.enquiry, "mobile", "")
        return ""

    def get_doctor_name(self, obj) -> str:
        if not obj.doctor:
            return ""
        if hasattr(obj.doctor, "user") and obj.doctor.user:
            return f"Dr. {obj.doctor.user.get_full_name() or obj.doctor.user.username}"
        return getattr(obj.doctor, "name", "Doctor")

    def get_department_name(self, obj) -> str:
        return obj.department.name if obj.department else ""

    def validate(self, attrs):
        # Auto-compute total_estimate if not explicitly passed or if components passed
        components = [
            attrs.get("surgeon_fee", getattr(self.instance, "surgeon_fee", 0)),
            attrs.get("ot_charges", getattr(self.instance, "ot_charges", 0)),
            attrs.get("room_charges", getattr(self.instance, "room_charges", 0)),
            attrs.get("medicines_estimate", getattr(self.instance, "medicines_estimate", 0)),
            attrs.get("implants_investigations", getattr(self.instance, "implants_investigations", 0)),
        ]
        calc_total = sum(c or 0 for c in components)
        if not attrs.get("total_estimate") or attrs.get("total_estimate") == 0:
            attrs["total_estimate"] = calc_total
        return attrs

