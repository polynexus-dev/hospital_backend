from rest_framework import serializers
from apps.patients.serializers import PatientSerializer
from .models import Bill, BillCreditNote, BillItem, InsuranceClaim, Payment


class BillItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillItem
        fields = ["id", "description", "quantity", "unit_price", "total_price", "tariff", "hsn_sac", "gst_rate", "tax_amount",
                  "doctor", "service_date", "source",
        ]
        read_only_fields = ["id", "total_price", "tax_amount", "source"]


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id", "bill", "amount", "payment_method", "transaction_id", "paid_at"]
        read_only_fields = ["id", "paid_at"]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Must be more than 0 (refunds go through a credit note).")
        return value


class InsuranceClaimSerializer(serializers.ModelSerializer):
    class Meta:
        model = InsuranceClaim
        fields = ["id", "bill", "insurance_company", "policy_number", "claimed_amount", "approved_amount", "status"]
        read_only_fields = ["id"]


class BillSerializer(serializers.ModelSerializer):
    items = BillItemSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    insurance_claim = InsuranceClaimSerializer(read_only=True)
    patient_detail = PatientSerializer(source="patient", read_only=True)

    class Meta:
        model = Bill
        fields = [
            "id",
            "patient",
            "patient_detail",
            "admission",
            "total_amount",
            "discount_amount",
            "net_amount",
            "status",
            "created_at",
            "items",
            "payments",
            "insurance_claim", "patient_category", "is_interim", "bill_number", "tax_amount",
            "on_hold", "hold_reason", "held_at",
        ]
        # The category changes through /change-category/ (which re-prices);
        # the hold through /hold/ and /release/.
        read_only_fields = ["id", "created_at", "is_interim", "bill_number", "tax_amount", "on_hold", "hold_reason", "held_at"]

    def validate_patient_category(self, value):
        if self.instance is not None and value != self.instance.patient_category:
            raise serializers.ValidationError("Use “Change category” so the bill is re-priced.")
        return value


class CreditNoteSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source="created_by.get_full_name", read_only=True, default="")

    class Meta:
        model = BillCreditNote
        exclude = ["hospital"]
