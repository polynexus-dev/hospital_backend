from rest_framework import serializers
from .models import Claim, PreAuthRequest, TPACompany


def active_tpa(serializer, value):
    """New pre-auths and claims can't go to an insurer/TPA marked inactive;
    existing ones keep theirs."""
    current = getattr(serializer.instance, "tpa_company_id", None)
    if value is not None and not value.is_active and value.pk != current:
        raise serializers.ValidationError(f"{value.name} is inactive. Reactivate it in the TPA list, or choose another.")
    return value


class TPACompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = TPACompany
        fields = [
            "id", "name", "code", "contact_person", "phone",
            "email", "claim_submission_email", "avg_tat_days",
            "is_active", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class PreAuthRequestSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    tpa_name = serializers.CharField(source="tpa_company.name", read_only=True)

    class Meta:
        model = PreAuthRequest
        fields = [
            "id", "patient", "patient_name", "tpa_company", "tpa_name",
            "policy_number", "claim_amount", "approved_amount",
            "status", "checklist", "submitted_at", "approved_at",
        ]
        read_only_fields = ["id", "submitted_at"]

    def validate_tpa_company(self, value):
        return active_tpa(self, value)


class ClaimSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    tpa_name = serializers.CharField(source="tpa_company.name", read_only=True)
    outstanding_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Claim
        fields = [
            "id", "preauth_request", "patient", "patient_name", "tpa_company", "tpa_name",
            "claim_number", "billed_amount", "settled_amount", "outstanding_amount",
            "status", "rejection_reason", "notes", "submitted_at", "settled_at",
        ]
        read_only_fields = ["id", "submitted_at"]

    def validate_tpa_company(self, value):
        return active_tpa(self, value)
