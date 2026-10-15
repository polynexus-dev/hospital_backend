from decimal import Decimal, InvalidOperation

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
            "email", "claim_submission_email", "avg_tat_days", "nhcx_participant_code",
            "is_active", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class PreAuthRequestSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    tpa_name = serializers.CharField(source="tpa_company.name", read_only=True)
    treating_doctor_name = serializers.CharField(source="treating_doctor.name", read_only=True, default="")
    open_queries = serializers.SerializerMethodField()
    claim_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)

    def get_open_queries(self, obj):
        return sum(1 for m in obj.messages.all() if m.is_open)

    class Meta:
        model = PreAuthRequest
        fields = [
            "id", "patient", "patient_name", "tpa_company", "tpa_name",
            "policy_number", "claim_amount", "approved_amount",
            "status", "checklist", "submitted_at", "approved_at",
            "member_id", "admission", "treating_doctor", "treating_doctor_name", "admission_type", "expected_admission_date",
            "expected_stay_days", "room_category", "diagnosis_code", "diagnosis", "procedure_name", "procedure_code",
            "clinical_summary", "cost_breakdown", "open_queries",
        ]
        read_only_fields = ["id", "submitted_at"]

    def validate_tpa_company(self, value):
        return active_tpa(self, value)

    def validate_cost_breakdown(self, value):
        if not isinstance(value, list) or not all(isinstance(l, dict) and l.get("amount") not in (None, "") for l in value):
            raise serializers.ValidationError('A list of {"category", "description", "amount"} rows.')
        try:
            [Decimal(str(l["amount"])) for l in value]
        except InvalidOperation:
            raise serializers.ValidationError("Amounts must be numbers.")
        return value

    def validate(self, attrs):
        user = getattr(self.context.get("request"), "user", None)
        for name in ("patient", "tpa_company", "admission", "treating_doctor"):
            obj = attrs.get(name)
            if obj is not None and user is not None and obj.hospital_id != user.hospital_id:
                raise serializers.ValidationError({name: "Refers to a record from a different hospital."})
        if attrs.get("cost_breakdown") and not attrs.get("claim_amount") and self.instance is None:
            attrs["claim_amount"] = sum((Decimal(str(l["amount"])) for l in attrs["cost_breakdown"]), Decimal("0"))
        if self.instance is None and attrs.get("claim_amount") is None:
            raise serializers.ValidationError({"claim_amount": "Enter the estimated amount or a cost breakdown."})
        return attrs


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
