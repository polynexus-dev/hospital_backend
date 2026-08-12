from rest_framework import serializers

from .models import Appointment, Doctor, Slot, SlotTemplate, Waitlist


class DoctorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Doctor
        fields = ["id", "department", "name", "speciality", "phone", "email", "default_consultation_minutes", "is_active"]
        read_only_fields = ["id"]


class SlotTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = SlotTemplate
        fields = ["id", "doctor", "weekday", "start_time", "end_time", "slot_duration_minutes", "is_active"]
        read_only_fields = ["id"]


class SlotSerializer(serializers.ModelSerializer):
    is_booked = serializers.BooleanField(read_only=True)

    class Meta:
        model = Slot
        fields = ["id", "doctor", "date", "start_time", "end_time", "is_blocked", "is_booked"]
        read_only_fields = ["id", "is_booked"]


class AppointmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Appointment
        fields = [
            "id", "patient", "doctor", "slot", "status", "source", "reason",
            "consent_captured", "consent_captured_at", "registration_token",
            "reminder_24h_sent_at", "reminder_2h_sent_at",
            "checked_in_at", "completed_at", "cancelled_at", "no_show_at",
            "rescheduled_from", "booked_by", "created_at",
        ]
        read_only_fields = [
            "id", "status", "registration_token", "reminder_24h_sent_at", "reminder_2h_sent_at",
            "checked_in_at", "completed_at", "cancelled_at", "no_show_at",
            "rescheduled_from", "booked_by", "created_at",
        ]


class WaitlistSerializer(serializers.ModelSerializer):
    class Meta:
        model = Waitlist
        fields = [
            "id", "patient", "doctor", "department", "preferred_date", "status",
            "offered_slot", "offered_at", "resulting_appointment", "notes", "created_at",
        ]
        read_only_fields = ["id", "status", "offered_slot", "offered_at", "resulting_appointment", "created_at"]


class BookAppointmentSerializer(serializers.Serializer):
    patient = serializers.IntegerField()
    slot = serializers.IntegerField()
    source = serializers.ChoiceField(choices=Appointment.Source.choices, default=Appointment.Source.CRM)
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class RescheduleAppointmentSerializer(serializers.Serializer):
    new_slot = serializers.IntegerField()


class GenerateSlotsSerializer(serializers.Serializer):
    weeks_ahead = serializers.IntegerField(default=4, min_value=1, max_value=12)
