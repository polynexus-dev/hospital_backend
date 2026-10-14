from rest_framework import serializers

from .models import Document, DocumentRequirement, Patient, TimelineEvent


class PatientSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Patient
        fields = [
            "id", "title", "first_name", "last_name", "full_name", "date_of_birth", "dob_is_estimated", "gender",
            "mobile", "alternate_mobile", "whatsapp", "email", "address", "city",
            "country", "state", "district", "sub_district", "village_or_area", "pincode", "nationality", "passport_number",
            "is_vvip", "tags",
            "national_id_type", "national_id_number",
            "insurance_provider", "insurance_policy_number", "employer",
            "attendant_name", "attendant_phone", "attendant_relation", "referring_doctor_name",
            "guardian", "relationship_to_guardian",
            "next_recall_due_at", "recall_reason",
            "preferred_language", "is_active", "created_at", "updated_at",
            "uhid", "mrn", "registration_type", "blood_group", "registration_channel", "payment_preference", "mobile_verified_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "uhid", "mobile_verified_at"]

    def validate_pincode(self, value):
        if value and (len(value) != 6 or not value.isdigit()):
            raise serializers.ValidationError("A PIN code has 6 digits.")
        return value

    def validate_passport_number(self, value):
        return value.strip().upper()

    def validate_guardian(self, guardian):
        if guardian is not None and self.instance is not None and guardian.pk == self.instance.pk:
            raise serializers.ValidationError("A patient cannot be their own guardian.")
        return guardian


class PatientLookupSerializer(serializers.ModelSerializer):
    """Minimal shape for the telephony screen-pop — fast, low-payload."""

    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Patient
        fields = ["id", "full_name", "mobile", "preferred_language", "insurance_provider", "referring_doctor_name"]


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ["id", "patient", "category", "title", "file", "notes", "uploaded_by", "requirement", "uploaded_via_portal", "created_at"]
        read_only_fields = ["id", "uploaded_by", "uploaded_via_portal", "created_at"]

    def validate(self, attrs):
        req = attrs.get("requirement")
        patient = attrs.get("patient") or getattr(self.instance, "patient", None)
        if req is not None and patient is not None and req.hospital_id != patient.hospital_id:
            raise serializers.ValidationError({"requirement": "Not one of this hospital's checklist items."})
        if req is not None and not attrs.get("category"):
            attrs["category"] = req.category
        return attrs


class TimelineEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimelineEvent
        fields = ["id", "patient", "event_type", "summary", "occurred_at", "created_by"]
        read_only_fields = fields


class PrescriptionSerializer(serializers.ModelSerializer):
    doctor_name = serializers.CharField(source="doctor.get_full_name", read_only=True)
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)

    class Meta:
        from .models import Prescription
        model = Prescription
        fields = [
            "id", "patient", "patient_name", "doctor", "doctor_name",
            "diagnosis", "symptoms", "medications", "lab_orders",
            "notes", "created_at",
        ]
        read_only_fields = ["id", "created_at"]




class DocumentRequirementSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentRequirement
        fields = ["id", "stage", "name", "category", "required", "only_if_insured", "valid_days", "reuse_same_category", "help_text", "order", "is_active"]
        read_only_fields = ["id"]
