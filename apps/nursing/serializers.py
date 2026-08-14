from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers

from apps.ipd.models import Admission

from .models import IntakeOutput, MedicationAdministration, NursingNote


class _AdmissionScopedSerializer(serializers.ModelSerializer):
    """`content_type`/`object_id` are the generic-relation storage, not a
    usable API shape — the client just says which Admission this is for
    (the only content type Phase 4 actually has; EDVisit/ICUAdmission
    arrive in Phase 6) and this resolves it server-side."""

    admission = serializers.PrimaryKeyRelatedField(queryset=Admission.objects.all(), write_only=True)
    admission_id = serializers.SerializerMethodField()

    def get_admission_id(self, obj):
        return obj.object_id

    def create(self, validated_data):
        admission = validated_data.pop("admission")
        validated_data["content_type"] = ContentType.objects.get_for_model(Admission)
        validated_data["object_id"] = str(admission.pk)
        return super().create(validated_data)


class NursingNoteSerializer(_AdmissionScopedSerializer):
    class Meta:
        model = NursingNote
        fields = ["id", "admission", "admission_id", "nurse", "note", "created_at"]
        read_only_fields = ["id", "nurse", "created_at"]


class MedicationAdministrationSerializer(_AdmissionScopedSerializer):
    class Meta:
        model = MedicationAdministration
        fields = ["id", "admission", "admission_id", "prescription", "medication_name", "dose", "nurse", "administered_at", "notes"]
        read_only_fields = ["id", "nurse", "administered_at"]


class IntakeOutputSerializer(_AdmissionScopedSerializer):
    class Meta:
        model = IntakeOutput
        fields = ["id", "admission", "admission_id", "recorded_by", "intake_ml", "output_ml", "recorded_at", "notes"]
        read_only_fields = ["id", "recorded_by", "recorded_at"]
