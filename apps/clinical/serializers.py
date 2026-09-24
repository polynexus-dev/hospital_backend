from django.utils import timezone
from rest_framework import serializers

from apps.core.crud import TenantModelSerializer

from . import scoring
from .models import (
    Allergy,
    AssessmentTemplate,
    CarePlan,
    ClinicalAlert,
    ClinicalAssessment,
    ConsentRecord,
    DigitalSignature,
    DrugConditionRule,
    DrugInteraction,
    EpisodeOfCare,
    FunctionalAssessment,
    HomecareBooking,
    HomecareService,
    NotifiableDisease,
    NotifiableDiseaseReport,
    OrderSet,
    ResultReview,
    RiskAssessment,
    ShiftHandover,
)


class PatientLabelMixin(serializers.Serializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    patient_uhid = serializers.CharField(source="patient.uhid", read_only=True)


def _user_name(user):
    return user.get_full_name() if user else None


class EpisodeOfCareSerializer(PatientLabelMixin, TenantModelSerializer):
    visit_count = serializers.SerializerMethodField()

    class Meta:
        model = EpisodeOfCare
        fields = "__all__"
        read_only_fields = ["episode_code"]

    def get_visit_count(self, obj):
        return obj.encounters.count() + obj.admissions.count() if hasattr(obj, "encounters") else 0


class AllergySerializer(PatientLabelMixin, TenantModelSerializer):
    class Meta:
        model = Allergy
        fields = "__all__"
        read_only_fields = ["recorded_by"]


class AssessmentTemplateSerializer(TenantModelSerializer):
    class Meta:
        model = AssessmentTemplate
        fields = "__all__"

    def validate_fields(self, value):
        allowed = {"text", "textarea", "number", "select", "multiselect", "boolean", "date"}
        for f in value:
            if not isinstance(f, dict) or not f.get("key") or f.get("type") not in allowed:
                raise serializers.ValidationError("Each field needs a key and a type in " + ", ".join(sorted(allowed)))
        return value


class ClinicalAssessmentSerializer(PatientLabelMixin, TenantModelSerializer):
    assessed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ClinicalAssessment
        fields = "__all__"
        read_only_fields = ["assessed_by"]

    def get_assessed_by_name(self, obj):
        return _user_name(obj.assessed_by)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        template = attrs.get("template") or getattr(self.instance, "template", None)
        if template:
            data = attrs.get("data", {}) or {}
            missing = [f.get("label") or f["key"] for f in template.fields if f.get("required") and data.get(f["key"]) in (None, "", [])]
            if missing:
                raise serializers.ValidationError({"data": f"Required: {', '.join(missing)}"})
            attrs.setdefault("category", template.category)
        return attrs


class RiskAssessmentSerializer(PatientLabelMixin, TenantModelSerializer):
    is_high_risk = serializers.SerializerMethodField()

    class Meta:
        model = RiskAssessment
        fields = "__all__"
        read_only_fields = ["score", "risk_level", "assessed_by"]

    def get_is_high_risk(self, obj):
        return obj.risk_level in scoring.HIGH_RISK_LEVELS

    def validate(self, attrs):
        attrs = super().validate(attrs)
        tool = attrs.get("tool") or getattr(self.instance, "tool", None)
        answers = attrs.get("answers", getattr(self.instance, "answers", {}))
        try:
            attrs["score"], attrs["risk_level"] = scoring.score(tool, answers)
        except ValueError as exc:
            raise serializers.ValidationError({"tool": str(exc)})
        return attrs


class OrderSetSerializer(TenantModelSerializer):
    class Meta:
        model = OrderSet
        fields = "__all__"
        read_only_fields = ["created_by"]


class ConsentRecordSerializer(PatientLabelMixin, TenantModelSerializer):
    patient_is_minor = serializers.SerializerMethodField()

    class Meta:
        model = ConsentRecord
        fields = "__all__"
        read_only_fields = ["obtained_by", "withdrawn_at"]

    def get_patient_is_minor(self, obj):
        return _is_minor(obj.patient)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        patient = attrs.get("patient") or getattr(self.instance, "patient", None)
        lacks_capacity = attrs.get("patient_lacks_capacity", getattr(self.instance, "patient_lacks_capacity", False))
        given_by = attrs.get("given_by", getattr(self.instance, "given_by", ConsentRecord.GivenBy.PATIENT))
        if patient is not None and (_is_minor(patient) or lacks_capacity):
            if given_by != ConsentRecord.GivenBy.GUARDIAN:
                raise serializers.ValidationError({"given_by": "Patient is a minor or lacks capacity — consent must be given by a legal guardian."})
            if not (attrs.get("guardian_name") or getattr(self.instance, "guardian_name", "")) or not (attrs.get("guardian_relation") or getattr(self.instance, "guardian_relation", "")):
                raise serializers.ValidationError({"guardian_name": "Guardian name and relationship are required."})
        return attrs


def _is_minor(patient):
    if not patient.date_of_birth:
        return False
    today = timezone.localdate()
    dob = patient.date_of_birth
    return (today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))) < 18


class ShiftHandoverSerializer(TenantModelSerializer):
    patient_name = serializers.CharField(source="admission.patient.full_name", read_only=True)
    handed_over_by_name = serializers.SerializerMethodField()
    handed_over_to_name = serializers.SerializerMethodField()

    class Meta:
        model = ShiftHandover
        fields = "__all__"
        read_only_fields = ["handed_over_by", "acknowledged_at"]

    def get_handed_over_by_name(self, obj):
        return _user_name(obj.handed_over_by)

    def get_handed_over_to_name(self, obj):
        return _user_name(obj.handed_over_to)


class CarePlanSerializer(PatientLabelMixin, TenantModelSerializer):
    class Meta:
        model = CarePlan
        fields = "__all__"
        read_only_fields = ["created_by"]


class DrugInteractionSerializer(TenantModelSerializer):
    class Meta:
        model = DrugInteraction
        fields = "__all__"


class DrugConditionRuleSerializer(TenantModelSerializer):
    class Meta:
        model = DrugConditionRule
        fields = "__all__"


class ClinicalAlertSerializer(TenantModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True, default=None)
    patient_uhid = serializers.CharField(source="patient.uhid", read_only=True, default=None)
    acknowledged_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ClinicalAlert
        fields = "__all__"
        read_only_fields = ["acknowledged_by", "acknowledged_at", "content_type", "object_id"]

    def get_acknowledged_by_name(self, obj):
        return _user_name(obj.acknowledged_by)


class NotifiableDiseaseSerializer(TenantModelSerializer):
    class Meta:
        model = NotifiableDisease
        fields = "__all__"


class NotifiableDiseaseReportSerializer(PatientLabelMixin, TenantModelSerializer):
    disease_name = serializers.CharField(source="disease.name", read_only=True)
    authority = serializers.CharField(source="disease.authority", read_only=True)

    class Meta:
        model = NotifiableDiseaseReport
        fields = "__all__"
        read_only_fields = ["reported_by", "reported_at"]


class ResultReviewSerializer(PatientLabelMixin, TenantModelSerializer):
    reviewed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ResultReview
        fields = "__all__"
        read_only_fields = ["reviewed_by"]

    def get_reviewed_by_name(self, obj):
        return _user_name(obj.reviewed_by)


class DigitalSignatureSerializer(serializers.ModelSerializer):
    document_type = serializers.CharField(source="content_type.model", read_only=True)
    is_valid = serializers.SerializerMethodField()

    class Meta:
        model = DigitalSignature
        exclude = ["hospital"]
        read_only_fields = [f.name for f in DigitalSignature._meta.fields]

    def get_is_valid(self, obj):
        from .signatures import document_hash

        doc = obj.document
        return doc is not None and document_hash(doc) == obj.document_hash


class HomecareServiceSerializer(TenantModelSerializer):
    class Meta:
        model = HomecareService
        fields = "__all__"


class HomecareBookingSerializer(PatientLabelMixin, TenantModelSerializer):
    service_name = serializers.CharField(source="service.name", read_only=True)
    assigned_staff_name = serializers.SerializerMethodField()

    class Meta:
        model = HomecareBooking
        fields = "__all__"
        read_only_fields = ["completed_at", "bill"]

    def get_assigned_staff_name(self, obj):
        return _user_name(obj.assigned_staff)


class FunctionalAssessmentSerializer(PatientLabelMixin, TenantModelSerializer):
    change_from_previous = serializers.SerializerMethodField()

    class Meta:
        model = FunctionalAssessment
        fields = "__all__"
        read_only_fields = ["assessed_by"]

    def get_change_from_previous(self, obj):
        if obj.previous_id and obj.total is not None and obj.previous.total is not None:
            return obj.total - obj.previous.total
        return None

    def validate(self, attrs):
        attrs = super().validate(attrs)
        scale = attrs.get("scale") or getattr(self.instance, "scale", None)
        if scale == FunctionalAssessment.Scale.BARTHEL:
            attrs["total"] = scoring.barthel_total(attrs.get("scores", getattr(self.instance, "scores", {})))
        return attrs
