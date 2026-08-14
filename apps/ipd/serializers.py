from rest_framework import serializers

from .models import Admission, BedAllocation, DischargeSummary, DoctorProgressNote, WardTransfer


class AdmissionSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    doctor_name = serializers.CharField(source="admitting_doctor.name", read_only=True)
    bed_label = serializers.CharField(source="bed.__str__", read_only=True)

    class Meta:
        model = Admission
        fields = [
            "id", "patient", "patient_name", "admitting_doctor", "doctor_name", "department",
            "bed", "bed_label", "source_encounter", "admission_type", "status", "admission_diagnosis",
            "admitted_at", "discharged_at",
        ]
        read_only_fields = ["id", "bed", "status", "admitted_at", "discharged_at"]


class AdmitPatientSerializer(serializers.Serializer):
    """Input shape for POST /ipd/admissions/ — bed availability locking
    and BedAllocation/Bed-status bookkeeping happen in
    apps.ipd.services.admit_patient, not here."""

    patient = serializers.IntegerField()
    admitting_doctor = serializers.IntegerField()
    bed = serializers.IntegerField()
    department = serializers.IntegerField(required=False, allow_null=True)
    admission_type = serializers.ChoiceField(choices=Admission.AdmissionType.choices, default=Admission.AdmissionType.PLANNED)
    admission_diagnosis = serializers.CharField(required=False, allow_blank=True, default="")
    source_encounter = serializers.IntegerField(required=False, allow_null=True)


class DischargeAdmissionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[c for c in Admission.Status.choices if c[0] != Admission.Status.ADMITTED],
        default=Admission.Status.DISCHARGED,
    )


class BedAllocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = BedAllocation
        fields = ["id", "admission", "bed", "allocated_at", "released_at"]
        read_only_fields = fields


class WardTransferSerializer(serializers.ModelSerializer):
    class Meta:
        model = WardTransfer
        fields = ["id", "admission", "from_bed", "to_bed", "reason", "requested_by", "approved_by", "requested_at", "transferred_at"]
        read_only_fields = ["id", "from_bed", "requested_by", "approved_by", "requested_at", "transferred_at"]


class DoctorProgressNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = DoctorProgressNote
        fields = ["id", "admission", "doctor", "note", "finalized_at", "finalized_by", "created_at"]
        read_only_fields = ["id", "doctor", "finalized_at", "finalized_by", "created_at"]


class DischargeSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = DischargeSummary
        fields = [
            "id", "admission", "final_diagnosis", "procedures_performed", "treatment_summary",
            "discharge_medications", "follow_up_instructions", "discharge_type",
            "prepared_by", "finalized_at", "finalized_by", "created_at",
        ]
        read_only_fields = ["id", "prepared_by", "finalized_at", "finalized_by", "created_at"]
