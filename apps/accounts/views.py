import pyotp
from django.contrib.auth import update_session_auth_hash
from django.core.signing import BadSignature
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.core.viewsets import TenantScopedViewSetMixin

from .models import Role, User
from apps.governance import services as gov
from apps.governance.models import SecurityEvent

from .serializers import (
    AdminSetPasswordSerializer,
    ChangePasswordSerializer,
    ExpiredPasswordChangeSerializer,
    HospitalScopedTokenObtainPairSerializer,
    RoleSerializer,
    UserSerializer,
    read_mfa_challenge_user_id,
)


class HospitalTokenObtainPairView(TokenObtainPairView):
    """Login has no auth to gate it (that's the point), which makes it the
    default brute-force target — a fixed, tight per-IP rate limit here
    (see REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["login"] in settings)
    matters more than the general API throttle every other endpoint gets.

    Deliberately doesn't set `throttle_classes` directly — ScopedRateThrottle
    is already in the global DEFAULT_THROTTLE_CLASSES (see base.py), and
    it's a no-op for any view that doesn't declare `throttle_scope`, so
    listing it here again would just shadow the setting-level default and
    make it impossible to turn off centrally (config.settings.test does
    exactly that, to keep the full suite from tripping this same limit —
    it disables DEFAULT_THROTTLE_CLASSES wholesale, which only works if no
    view hardcodes its own throttle_classes list)."""

    serializer_class = HospitalScopedTokenObtainPairSerializer
    throttle_scope = "login"

    def post(self, request, *args, **kwargs):
        """Failed-login counters/lockouts and security events are written
        while the request is *failing*. Raising AuthenticationFailed out of
        the view would make DRF's exception handler mark the request's
        transaction (ATOMIC_REQUESTS) for rollback and discard exactly those
        writes — so the lockout would never trigger. Returning the 401
        instead keeps them committed."""
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except AuthenticationFailed as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
            return Response(detail, status=exc.status_code)
        except TokenError as exc:
            raise InvalidToken(exc.args[0])
        return Response(serializer.validated_data, status=status.HTTP_200_OK)


class UserViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = UserSerializer
    queryset = User.objects.all()  # metadata for DjangoModelPermissions; get_queryset() below does the real (tenant-scoped) filtering
    filterset_fields = ["department", "role", "is_active"]
    search_fields = ["email", "phone", "first_name", "last_name"]

    def get_queryset(self):
        user = self.request.user
        # select_related: UserSerializer's role_name/hospital_name/
        # hospital_address/hospital_city/hospital_state (source="role.name",
        # "hospital.*") would otherwise re-query per row on every list page.
        base = User.objects.select_related("role", "hospital")
        if user.can_cross_tenant:
            return base
        return base.filter(hospital_id=user.hospital_id)

    def _require_saas_owner_for_platform_identity(self, request, target=None):
        """Owner → Manager → Support Lead delegated account management.
        The target role, rather than the caller's `is_staff` bit, defines
        the boundary so no one can grant lateral or upward privilege."""
        requested_role = str(request.data.get("saas_role", ""))
        target_role = requested_role or str(getattr(target, "saas_role", ""))
        if target_role and not request.user.can_manage_saas_role(target_role):
            return Response({"detail": "You may only manage SaaS roles below your delegated access level."}, status=status.HTTP_403_FORBIDDEN)
        return None

    def _log_saas_access_change(self, request, target, action):
        gov.log_security_event(
            SecurityEvent.EventType.SAAS_ACCESS_CHANGED,
            request=request,
            user=request.user,
            details={"action": action, "target_user_id": target.pk, "target_email": target.email, "saas_role": target.saas_role},
        )

    def perform_create(self, serializer):
        denied = self._require_saas_owner_for_platform_identity(self.request)
        if denied:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied(denied.data["detail"])
        hospital = getattr(self.request.user, "hospital", None)
        if hospital:
            from apps.saas_admin.models import TenantSubscription
            from rest_framework.exceptions import ValidationError
            sub = TenantSubscription.objects.filter(hospital=hospital, status=TenantSubscription.Status.ACTIVE).first()
            if sub and sub.max_staff_users > 0:
                current_count = User.objects.filter(hospital=hospital, is_active=True).count()
                if current_count >= sub.max_staff_users:
                    raise ValidationError({"detail": f"Hospital has reached its subscription staff limit of {sub.max_staff_users} users."})
        if str(self.request.data.get("saas_role", "")):
            created_user = serializer.save(hospital=None, is_saas_admin=True)
        else:
            created_user = serializer.save(hospital=hospital)
        if created_user.saas_role:
            self._log_saas_access_change(self.request, created_user, "created")

    def perform_update(self, serializer):
        denied = self._require_saas_owner_for_platform_identity(self.request, serializer.instance)
        if denied:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied(denied.data["detail"])
        if str(self.request.data.get("saas_role", "")):
            updated_user = serializer.save(hospital=None, is_saas_admin=True)
        else:
            updated_user = serializer.save()
        if updated_user.saas_role:
            self._log_saas_access_change(self.request, updated_user, "updated")

    def perform_destroy(self, instance):
        denied = self._require_saas_owner_for_platform_identity(self.request, instance)
        if denied:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied(denied.data["detail"])
        if instance.saas_role:
            self._log_saas_access_change(self.request, instance, "deleted")
        instance.delete()

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def me(self, request):
        return Response(UserSerializer(request.user).data)

    @action(detail=False, methods=["post"], url_path="switch-hospital", permission_classes=[IsAuthenticated])
    def switch_hospital(self, request):
        """Platform-ops only (User.can_cross_tenant). This used to accept
        any authenticated user and reassign their `hospital` FK to *any*
        active hospital's id with no further check — since Hospital has no
        group/ownership concept in the schema, that meant any front-desk
        user at any hospital could call this with an arbitrary hospital_id
        and permanently switch themselves into a completely unrelated
        hospital's tenant, gaining full read/write access to its data
        through every other endpoint (verified empirically, not
        theoretical). That was then "fixed" by restricting to is_staff to
        match the X-Hospital-Id header in TenantMiddleware — except
        is_staff is also granted to every hospital's own Owner account
        (apps.saas_admin.tenant_service), so an ordinary hospital Owner
        could still do exactly this. can_cross_tenant is the actual
        platform-ops-only check; see its docstring."""
        if not request.user.has_saas_capability("hospital_access"):
            return Response({"detail": "Only platform staff may switch hospitals."}, status=status.HTTP_403_FORBIDDEN)

        reason = str(request.data.get("reason", "")).strip()
        if len(reason) < 10:
            return Response({"reason": "Provide a support-access reason of at least 10 characters."}, status=status.HTTP_400_BAD_REQUEST)

        hospital_id = request.data.get("hospital_id")
        if not hospital_id:
            return Response({"detail": "hospital_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        from apps.core.models import Hospital
        try:
            target_hospital = Hospital.objects.get(id=hospital_id, is_active=True)
        except Hospital.DoesNotExist:
            return Response({"detail": "Hospital branch not found."}, status=status.HTTP_404_NOT_FOUND)

        request.user.hospital = target_hospital
        request.user.save(update_fields=["hospital"])
        gov.log_security_event(
            SecurityEvent.EventType.SUPPORT_ACCESS,
            request=request,
            user=request.user,
            hospital_id=target_hospital.pk,
            details={"reason": reason, "access": "hospital_operations"},
        )
        return Response(UserSerializer(request.user).data)


    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated])
    def change_password(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"user": request.user})
        serializer.is_valid(raise_exception=True)
        user = request.user
        if not user.check_password(serializer.validated_data["old_password"]):
            return Response({"old_password": "Incorrect password."}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])
        gov.record_password_change(user, request=request)
        update_session_auth_hash(request, user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _require_user_admin(self, request):
        if not (request.user.is_superuser or request.user.has_perm("accounts.change_user")):
            return Response({"detail": "You do not have permission to manage users."}, status=status.HTTP_403_FORBIDDEN)
        return None

    @action(detail=True, methods=["post"], url_path="set-password")
    def set_password(self, request, pk=None):
        """Administrator reset (DOM.4.d centralized user management)."""
        denied = self._require_user_admin(request)
        if denied:
            return denied
        target = self.get_object()
        serializer = AdminSetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from django.contrib.auth.password_validation import validate_password

        validate_password(serializer.validated_data["new_password"], user=target)
        target.set_password(serializer.validated_data["new_password"])
        target.save(update_fields=["password"])
        gov.record_password_change(target, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def block(self, request, pk=None):
        denied = self._require_user_admin(request)
        if denied:
            return denied
        target = self.get_object()
        if target.pk == request.user.pk:
            return Response({"detail": "You cannot block your own account."}, status=status.HTTP_400_BAD_REQUEST)
        target.is_blocked = True
        target.blocked_reason = str(request.data.get("reason", ""))[:255]
        target.save(update_fields=["is_blocked", "blocked_reason"])
        gov.log_security_event(SecurityEvent.EventType.USER_BLOCKED, request=request, user=target, details={"by": request.user.email, "reason": target.blocked_reason})
        return Response(UserSerializer(target).data)

    @action(detail=True, methods=["post"])
    def unblock(self, request, pk=None):
        denied = self._require_user_admin(request)
        if denied:
            return denied
        target = self.get_object()
        target.is_blocked = False
        target.blocked_reason = ""
        target.save(update_fields=["is_blocked", "blocked_reason"])
        gov.log_security_event(SecurityEvent.EventType.USER_UNBLOCKED, request=request, user=target, details={"by": request.user.email})
        return Response(UserSerializer(target).data)

    @action(detail=True, methods=["post"])
    def unlock(self, request, pk=None):
        denied = self._require_user_admin(request)
        if denied:
            return denied
        target = self.get_object()
        gov.reset_failed_logins(target)
        gov.log_security_event(SecurityEvent.EventType.ACCOUNT_UNLOCKED, request=request, user=target, details={"by": request.user.email})
        target.refresh_from_db()
        return Response(UserSerializer(target).data)

    @action(detail=False, methods=["post"], url_path="verify-password", permission_classes=[IsAuthenticated])
    def verify_password(self, request):
        """Unlocks the auto-locked screen (DOM.4.b) — re-authenticates the
        already signed-in user without issuing new tokens. The failure
        event is written even though the request is atomic: a 400 Response
        (not a raised exception) doesn't mark the transaction for rollback."""
        if request.user.check_password(str(request.data.get("password", ""))):
            return Response({"ok": True})
        gov.log_security_event(SecurityEvent.EventType.SCREEN_UNLOCK_FAILED, request=request, user=request.user)
        return Response({"password": "Incorrect password."}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["post"], url_path="2fa/setup", permission_classes=[IsAuthenticated])
    def setup_2fa(self, request):
        """Step 1 of enrollment (Part A #7): generates a new TOTP secret and
        returns it plus a provisioning URI (for a QR code) — NOT yet
        enabled. A generated-but-unconfirmed secret can't be used to log
        in; is_2fa_enabled only flips on in enable_2fa below, once the user
        proves they actually scanned it by supplying a valid code."""
        user = request.user
        secret = pyotp.random_base32()
        user.totp_secret = secret
        user.save(update_fields=["totp_secret"])
        uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="Polynexus HMS")
        return Response({"secret": secret, "provisioning_uri": uri})

    @action(detail=False, methods=["post"], url_path="2fa/enable", permission_classes=[IsAuthenticated])
    def enable_2fa(self, request):
        user = request.user
        if not user.totp_secret:
            return Response({"detail": "Call 2fa/setup first."}, status=status.HTTP_400_BAD_REQUEST)
        otp = str(request.data.get("otp", "")).strip()
        if not pyotp.TOTP(user.totp_secret).verify(otp, valid_window=1):
            return Response({"otp": "Invalid code."}, status=status.HTTP_400_BAD_REQUEST)
        user.is_2fa_enabled = True
        user.save(update_fields=["is_2fa_enabled"])
        return Response({"is_2fa_enabled": True})

    @action(detail=False, methods=["post"], url_path="2fa/disable", permission_classes=[IsAuthenticated])
    def disable_2fa(self, request):
        """Requires the current password, not just an authenticated
        session — this turns off a security control, which is exactly the
        situation a stolen/left-open session shouldn't be able to exploit
        on its own (mirrors change_password's own-credential check above)."""
        password = str(request.data.get("password", ""))
        if not request.user.check_password(password):
            return Response({"password": "Incorrect password."}, status=status.HTTP_400_BAD_REQUEST)
        user = request.user
        user.is_2fa_enabled = False
        user.totp_secret = ""
        user.save(update_fields=["is_2fa_enabled", "totp_secret"])
        return Response({"is_2fa_enabled": False})


class MFAVerifyView(APIView):
    """Step 2 of login for any account with is_2fa_enabled=True (Part A
    #7) — exchanges the mfa_token from HospitalScopedTokenObtainPairSerializer
    plus a valid OTP for real access/refresh tokens. No session/auth of its
    own yet (that's the whole point), so AllowAny + no authentication — the
    mfa_token itself (short-lived, signed, single-purpose) is the only
    credential this endpoint trusts."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "login"  # same brute-force concern as the login view itself

    def post(self, request):
        mfa_token = str(request.data.get("mfa_token", ""))
        otp = str(request.data.get("otp", "")).strip()

        try:
            user_id = read_mfa_challenge_user_id(mfa_token)
        except BadSignature:
            return Response({"detail": "Invalid or expired MFA challenge."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(pk=user_id, is_active=True)
        except User.DoesNotExist:
            return Response({"detail": "Invalid or expired MFA challenge."}, status=status.HTTP_400_BAD_REQUEST)

        if not user.is_2fa_enabled or not user.totp_secret:
            return Response({"detail": "MFA is not enabled for this account."}, status=status.HTTP_400_BAD_REQUEST)

        if not pyotp.TOTP(user.totp_secret).verify(otp, valid_window=1):
            gov.log_security_event(SecurityEvent.EventType.MFA_FAILED, request=request, user=user)
            return Response({"otp": "Invalid code."}, status=status.HTTP_400_BAD_REQUEST)

        gov.log_security_event(SecurityEvent.EventType.LOGIN_SUCCESS, request=request, user=user, details={"mfa": True})
        refresh = HospitalScopedTokenObtainPairSerializer.get_token(user)
        return Response({"refresh": str(refresh), "access": str(refresh.access_token)})


class RoleViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = RoleSerializer
    queryset = Role.objects.all()  # metadata for DjangoModelPermissions; get_queryset() below does the real (tenant-scoped) filtering
    filterset_fields = ["department"]

    def get_queryset(self):
        user = self.request.user
        if user.can_cross_tenant:
            return Role.objects.all()
        return Role.objects.filter(hospital_id=user.hospital_id)


class ExpiredPasswordChangeView(APIView):
    """DOM.4.a — the only way past a `password_expired` login response.
    Takes the old credentials (there's no session yet), enforces the full
    policy on the new password, then the user signs in normally."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "login"

    def post(self, request):
        serializer = ExpiredPasswordChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = User.objects.filter(email__iexact=data["email"], is_active=True).first()
        if user is None or user.is_blocked or user.is_locked_out or not user.check_password(data["old_password"]):
            if user is not None and not user.is_blocked and not user.is_locked_out:
                gov.register_failed_login(user, request=request)
            return Response({"detail": "Invalid credentials."}, status=status.HTTP_400_BAD_REQUEST)
        if data["old_password"] == data["new_password"]:
            return Response({"new_password": ["New password must differ from the old one."]}, status=status.HTTP_400_BAD_REQUEST)
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError

        try:
            validate_password(data["new_password"], user=user)
        except DjangoValidationError as exc:
            return Response({"new_password": list(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(data["new_password"])
        user.save(update_fields=["password"])
        gov.record_password_change(user, request=request)
        gov.reset_failed_logins(user)
        return Response({"detail": "Password changed. Please sign in with your new password."})
