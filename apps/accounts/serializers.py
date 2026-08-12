from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import Role, User


class HospitalScopedTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["hospital_id"] = str(user.hospital_id) if user.hospital_id else None
        token["role"] = user.role.name if user.role_id else None
        token["is_staff"] = user.is_staff
        return token


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["id", "hospital", "department", "name", "description", "created_at"]
        read_only_fields = ["hospital"]


class UserSerializer(serializers.ModelSerializer):
    role_name = serializers.CharField(source="role.name", read_only=True)
    hospital_name = serializers.CharField(source="hospital.name", read_only=True)
    available_hospitals = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "email", "phone", "first_name", "last_name",
            "hospital", "hospital_name", "department", "role", "role_name",
            "preferred_language", "is_active", "is_staff", "available_hospitals", "date_joined",
        ]
        read_only_fields = ["id", "date_joined", "is_staff"]

    def get_available_hospitals(self, obj):
        from apps.core.models import Hospital
        return list(Hospital.objects.filter(is_active=True).values("id", "name", "slug", "city"))



class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)
