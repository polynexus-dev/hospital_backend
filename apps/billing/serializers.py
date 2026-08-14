from rest_framework import serializers
from apps.patients.serializers import PatientSerializer
from .models import Bill, BillItem, InsuranceClaim, Payment


class BillItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillItem
        fields = ["id", "description", "quantity", "unit_price", "total_price"]
        read_only_fields = ["id", "total_price"]


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id", "bill", "amount", "payment_method", "transaction_id", "paid_at"]
        read_only_fields = ["id", "paid_at"]


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
            "insurance_claim",
        ]
        read_only_fields = ["id", "created_at"]
