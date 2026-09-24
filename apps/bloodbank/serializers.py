from rest_framework import serializers

from apps.patients.serializers import PatientSerializer
from .models import BloodUnit, CrossMatchRequest, Donor, Transfusion


class DonorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Donor
        fields = ["id", "name", "blood_group", "phone", "last_donation_date", "created_at", "updated_at", "date_of_birth", "gender", "weight_kg", "haemoglobin", "is_eligible", "deferral_reason", "deferred_until", "is_voluntary",
        ]
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
            "updated_at", "unit_number", "volume_ml", "tti_screened", "storage_location",
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
            "updated_at", "units_requested", "urgency", "delay_reason", "sample_received_at", "grouping_done_at", "crossmatched_at", "reserved_unit", "issued_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "sample_received_at", "grouping_done_at", "crossmatched_at", "reserved_unit", "issued_at"]


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
            "updated_at", "bedside_verified_by", "pre_vitals", "post_vitals", "ended_at", "had_reaction", "reaction_type", "reaction_severity",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "had_reaction", "reaction_type", "reaction_severity"]
