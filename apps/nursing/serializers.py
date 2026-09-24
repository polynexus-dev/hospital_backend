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

    def validate_admission(self, admission):
        """`Admission.objects.all()` above is deliberately unscoped (DRF
        resolves the PK before any hospital is known), so without this
        check a nurse could target another hospital's Admission by its id
        — the created record would still be stamped with the *creating*
        user's hospital (see perform_create in views.py), but the
        generic-relation FK itself would point cross-tenant. Same pattern
        as apps.enquiries.serializers.ReassignEnquirySerializer.validate_owner."""
        request = self.context.get("request")
        if request is not None and admission.hospital_id != request.user.hospital_id:
            raise serializers.ValidationError("Admission must belong to your hospital.")
        return admission

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
    """eMAR (NABH MOM.3.a/b): the wristband scan must match the admitted
    patient's UHID — a mismatch is refused outright, never just warned —
    and high-risk medicines need a second nurse as witness."""

    def create(self, validated_data):
        admission = validated_data["admission"]
        patient = admission.patient
        method = validated_data.get("identity_method", "")
        scanned = (validated_data.get("scanned_identifier") or "").strip()
        if method == "wristband_scan":
            if scanned.upper() != (patient.uhid or "").upper():
                raise serializers.ValidationError({"scanned_identifier": f"WRONG PATIENT — scanned {scanned!r} does not match {patient.full_name} ({patient.uhid})."})
            validated_data["identity_verified"] = True
        elif method == "two_identifiers":
            validated_data["identity_verified"] = True
        from apps.pharmacy.models import Medicine

        name = (validated_data.get("medication_name") or "").lower()
        high_risk = any(m for m in Medicine.objects.filter(hospital_id=admission.hospital_id, is_high_risk=True).values_list("name", "generic_name")
                        if any(n and n.lower() in name for n in m))
        witness = validated_data.get("witnessed_by")
        request = self.context.get("request")
        if high_risk and (witness is None or (request is not None and witness.pk == request.user.pk)):
            raise serializers.ValidationError({"witnessed_by": "High-risk medication — a second nurse must witness the administration."})
        validated_data["patient"] = patient
        return super().create(validated_data)

    class Meta:
        model = MedicationAdministration
        fields = [
            "id", "admission", "admission_id", "prescription", "medication_name", "dose", "nurse", "administered_at", "notes",
            "patient", "route", "status", "scheduled_for", "identity_method", "scanned_identifier", "identity_verified", "witnessed_by",
        ]
        read_only_fields = ["id", "nurse", "administered_at", "patient", "identity_verified"]


class IntakeOutputSerializer(_AdmissionScopedSerializer):
    class Meta:
        model = IntakeOutput
        fields = ["id", "admission", "admission_id", "recorded_by", "intake_ml", "output_ml", "recorded_at", "notes"]
        read_only_fields = ["id", "recorded_by", "recorded_at"]
