from rest_framework import serializers

from apps.patients.serializers import PatientSerializer
from .models import BloodUnit, CrossMatchRequest, Donor, Transfusion


class DonorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Donor
        fields = ["id", "name", "blood_group", "phone", "last_donation_date", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class BloodUnitSerializer(serializers.ModelSerializer):
    donor_detail = DonorSerializer(source="donor", read_only=True)

    class Meta:
        model = BloodUnit
        fields = [
            "id",
            "donor",
            "donor_detail",
            "blood_group",
            "component",
            "collection_date",
            "expiry_date",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class CrossMatchRequestSerializer(serializers.ModelSerializer):
    patient_detail = PatientSerializer(source="patient", read_only=True)

    class Meta:
        model = CrossMatchRequest
        fields = [
            "id",
            "patient",
            "patient_detail",
            "blood_group_required",
            "component",
            "requested_by",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class TransfusionSerializer(serializers.ModelSerializer):
    patient_detail = PatientSerializer(source="patient", read_only=True)
    blood_unit_detail = BloodUnitSerializer(source="blood_unit", read_only=True)

    class Meta:
        model = Transfusion
        fields = [
            "id",
            "blood_unit",
            "blood_unit_detail",
            "patient",
            "patient_detail",
            "admission",
            "issued_by",
            "transfused_at",
            "reaction_notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
