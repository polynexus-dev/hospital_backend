from rest_framework import serializers
from .models import Expense, Ledger, Receivable


class LedgerSerializer(serializers.ModelResourceSerializer if hasattr(serializers, "ModelResourceSerializer") else serializers.ModelSerializer):
    class Meta:
        model = Ledger
        fields = ["id", "entry_type", "category", "amount", "reference_type", "reference_id", "entry_date"]
        read_only_fields = ["id", "entry_date"]


class ExpenseSerializer(serializers.ModelSerializer):
    paid_by_name = serializers.ReadOnlyField(source="paid_by.get_full_name")
    approved_by_name = serializers.ReadOnlyField(source="approved_by.get_full_name")

    class Meta:
        model = Expense
        fields = ["id", "category", "amount", "paid_to", "paid_by", "paid_by_name", "expense_date", "approved_by", "approved_by_name"]
        read_only_fields = ["id", "paid_by"]


class ReceivableSerializer(serializers.ModelSerializer):
    class Meta:
        model = Receivable
        fields = ["id", "source_type", "source_id", "amount", "due_date", "status"]
        read_only_fields = ["id"]
