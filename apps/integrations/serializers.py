from rest_framework import serializers

from .models import HISBillingRecord, HISVisit


class HISVisitSerializer(serializers.ModelSerializer):
    class Meta:
        model = HISVisit
        fields = ["id", "patient", "external_visit_id", "visit_type", "visit_date", "department_name", "doctor_name"]
        read_only_fields = fields


class HISBillingRecordSerializer(serializers.ModelSerializer):
    outstanding_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = HISBillingRecord
        fields = ["id", "patient", "external_bill_id", "bill_date", "total_amount", "paid_amount", "outstanding_amount", "status"]
        read_only_fields = fields
