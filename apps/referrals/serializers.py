from rest_framework import serializers
from .models import FieldVisit, ReferralRecord, ReferringDoctor


class ReferringDoctorSerializer(serializers.ModelSerializer):
    total_referrals = serializers.IntegerField(read_only=True, default=0)
    total_attributed_revenue = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True, default=0.00)

    class Meta:
        model = ReferringDoctor
        fields = [
            "id", "name", "speciality", "clinic_name", "mobile", "email",
            "city", "tier", "date_of_birth", "notes", "is_active",
            "total_referrals", "total_attributed_revenue", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class ReferralRecordSerializer(serializers.ModelSerializer):
    referring_doctor_name = serializers.CharField(source="referring_doctor.name", read_only=True)
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)

    class Meta:
        model = ReferralRecord
        fields = [
            "id", "referring_doctor", "referring_doctor_name", "patient",
            "patient_name", "department", "attributed_revenue",
            "commission_percentage", "status", "referred_at",
        ]
        read_only_fields = ["id", "referred_at"]


class FieldVisitSerializer(serializers.ModelSerializer):
    referring_doctor_name = serializers.CharField(source="referring_doctor.name", read_only=True)
    visited_by_name = serializers.CharField(source="visited_by.first_name", read_only=True)

    class Meta:
        model = FieldVisit
        fields = [
            "id", "referring_doctor", "referring_doctor_name", "visited_by",
            "visited_by_name", "visit_date", "notes", "outcome", "created_at",
        ]
        read_only_fields = ["id", "created_at"]
