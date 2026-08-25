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
    role_domain = serializers.CharField(source="role.domain", read_only=True, default=None)
    hospital_name = serializers.CharField(source="hospital.name", read_only=True)
    hospital_address = serializers.CharField(source="hospital.address", read_only=True)
    hospital_city = serializers.CharField(source="hospital.city", read_only=True)
    hospital_state = serializers.CharField(source="hospital.state", read_only=True)
    hospital_enabled_modules = serializers.ReadOnlyField(source="hospital.enabled_modules")
    available_hospitals = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "email", "phone", "first_name", "last_name",
            "hospital", "hospital_name", "hospital_address", "hospital_city", "hospital_state", "hospital_enabled_modules",
            "department", "role", "role_name", "role_domain",
            "preferred_language", "is_active", "is_staff", "is_saas_admin", "available_hospitals", "permissions", "date_joined",
        ]
        # `hospital` used to be writable here — perform_create already
        # silently overrides it on create regardless of what's posted, but
        # nothing did the same for update, so a PATCH to a user's own
        # record with a different `hospital` id worked (verified
        # empirically), completely bypassing switch_hospital's is_staff
        # gate through a second door. Read-only here forces every
        # hospital reassignment through that one, now-properly-gated,
        # action instead of two paths with different rules. is_saas_admin
        # read-only for the same class of reason — it must only ever be
        # set by another SaaS admin/superuser through Django admin or a
        # dedicated action, never by a user editing their own record.
        read_only_fields = ["id", "date_joined", "is_staff", "is_saas_admin", "hospital"]

    def get_available_hospitals(self, obj):
        """Staff (ops/superadmin) get every active hospital on the
        platform, matching the existing X-Hospital-Id cross-hospital
        convention. Everyone else gets only their own — there is no
        "branches of my group" concept in the schema (Hospital has no
        parent/group FK), so listing every tenant on the platform here
        would tell a front-desk user at one hospital the name and city of
        every other hospital sharing this deployment, and feed a
        switch-hospital UI that would let them try to switch into any of
        them (see UserViewSet.switch_hospital)."""
        from apps.core.models import Hospital

        if obj.is_staff:
            return list(Hospital.objects.filter(is_active=True).values("id", "name", "slug", "city"))
        if obj.hospital_id:
            return list(Hospital.objects.filter(id=obj.hospital_id, is_active=True).values("id", "name", "slug", "city"))
        return []

    def get_permissions(self, obj):
        """`app_label.codename` strings, from Django's own has_perm()
        machinery (group permissions + superuser bypass) — the frontend
        route/nav gate (see Frontend/src/app/ProtectedRoute.tsx) reads
        this rather than re-deriving access from role_name, so a backend
        permission change takes effect without a frontend redeploy."""
        return sorted(obj.get_all_permissions())



class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)
