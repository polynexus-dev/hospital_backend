from rest_framework import serializers

from .models import Bed, Department, Room, Ward


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "name", "code", "is_active"]
        read_only_fields = ["id"]


class WardSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ward
        fields = ["id", "name", "ward_type", "department", "floor", "is_active", "created_at"]
        read_only_fields = ["id", "created_at"]


class RoomSerializer(serializers.ModelSerializer):
    class Meta:
        model = Room
        fields = ["id", "ward", "room_number", "room_type", "is_active", "created_at"]
        read_only_fields = ["id", "created_at"]


class BedSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bed
        fields = ["id", "room", "bed_number", "bed_type", "status", "created_at"]
        read_only_fields = ["id", "created_at"]
