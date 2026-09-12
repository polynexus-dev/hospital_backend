from django.core import signing
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from apps.core.request_utils import get_client_ip

from .models import Role, User

MFA_CHALLENGE_SALT = "accounts.mfa_challenge"
MFA_CHALLENGE_MAX_AGE_SECONDS = 300


def make_mfa_challenge_token(user) -> str:
    """Short-lived, tamper-proof token identifying a user who has passed
    the password check but still owes an OTP (Part A #7). Deliberately not
    a JWT — it must NOT be usable as an API credential, only as a receipt
    to hand back to /auth/mfa/verify/."""
    return signing.dumps({"user_id": user.pk}, salt=MFA_CHALLENGE_SALT)


def read_mfa_challenge_user_id(token: str) -> int:
    """Raises django.core.signing.BadSignature (includes SignatureExpired)
    on an invalid or expired token — callers should catch that, not let it
    500."""
    data = signing.loads(token, salt=MFA_CHALLENGE_SALT, max_age=MFA_CHALLENGE_MAX_AGE_SECONDS)
    return data["user_id"]


class HospitalScopedTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["hospital_id"] = str(user.hospital_id) if user.hospital_id else None
        token["role"] = user.role.name if user.role_id else None
        token["is_staff"] = user.is_staff
        return token

    def validate(self, attrs):
        data = super().validate(attrs)  # raises AuthenticationFailed on bad credentials; self.user is now set

        request = self.context.get("request")
        client_ip = get_client_ip(request) if request else None
        if not self.user.is_login_ip_allowed(client_ip):
            raise AuthenticationFailed("Login is not permitted from this network for this account.")

        if self.user.is_2fa_enabled:
            # Real access/refresh tokens were already built by
            # super().validate() above — simply not returning them here
            # means they're discarded, never reaching the client. The
            # caller must complete MFAVerifyView with the OTP to get real
            # tokens back.
            return {"mfa_required": True, "mfa_token": make_mfa_challenge_token(self.user)}

        if self.user.requires_mfa:
            data["mfa_setup_required"] = True
        return data


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["id", "hospital", "department", "name", "description", "created_at"]
        read_only_fields = ["hospital"]


class UserSerializer(serializers.ModelSerializer):
    role_name = serializers.CharField(source="role.name", read_only=True)
    hospital_name = serializers.CharField(source="hospital.name", read_only=True)
    hospital_address = serializers.CharField(source="hospital.address", read_only=True)
    hospital_city = serializers.CharField(source="hospital.city", read_only=True)
    hospital_state = serializers.CharField(source="hospital.state", read_only=True)
    available_hospitals = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()
    role_domain = serializers.SerializerMethodField()
    hospital_enabled_modules = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "email", "phone", "first_name", "last_name",
            "hospital", "hospital_name", "hospital_address", "hospital_city", "hospital_state",
            "hospital_enabled_modules",
            "department", "role", "role_name", "role_domain", "permissions",
            "preferred_language", "is_active", "is_staff", "is_superuser", "is_saas_admin",
            "available_hospitals", "date_joined", "is_2fa_enabled", "requires_mfa",
        ]
        # `hospital` used to be writable here — perform_create already
        # silently overrides it on create regardless of what's posted, but
        # nothing did the same for update, so a PATCH to a user's own
        # record with a different `hospital` id worked (verified
        # empirically), completely bypassing switch_hospital's is_staff
        # gate through a second door. Read-only here forces every
        # hospital reassignment through that one, now-properly-gated,
        # action instead of two paths with different rules.
        #
        # is_2fa_enabled/requires_mfa are read-only for the same reason —
        # they only change through UserViewSet's setup_2fa/enable_2fa/
        # disable_2fa actions (which prove control of the authenticator,
        # or the current password), never a bare PATCH. totp_secret itself
        # is never serialized here at all — it's not in `fields` above.
        #
        # is_superuser/is_saas_admin are exposed (previously omitted from
        # `fields` entirely, so `/users/me/` never told the frontend
        # whether the caller was one) but, same as is_staff, read-only —
        # a bare PATCH granting yourself either would be a direct
        # privilege escalation, exactly the class of bug `hospital`'s
        # comment above already describes for this same serializer.
        read_only_fields = [
            "id", "date_joined", "is_staff", "is_superuser", "is_saas_admin",
            "hospital", "is_2fa_enabled", "requires_mfa",
        ]

    def get_permissions(self, obj):
        """Flat `app_label.codename` strings — PermissionsMixin already
        computes exactly this shape (and already special-cases
        is_superuser to return every permission), so this just surfaces
        what Django's own auth backend already derives from the user's
        role group, no new logic. Frontend nav-gating (navConfig.hasNavAccess)
        and route-gating (RequirePermission) both key off this."""
        return sorted(obj.get_all_permissions())

    def get_role_domain(self, obj):
        """Drives the CRM/ERP sidebar switch (see Role.domain's docstring)
        — None when the user has no role at all (e.g. a bare superuser)."""
        return obj.role.domain if obj.role_id else None

    def get_hospital_enabled_modules(self, obj):
        return obj.hospital.enabled_modules if obj.hospital_id else []

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



class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)
