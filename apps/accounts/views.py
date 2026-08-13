from django.contrib.auth import update_session_auth_hash
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.core.viewsets import TenantScopedViewSetMixin

from .models import Role, User
from .serializers import (
    ChangePasswordSerializer,
    HospitalScopedTokenObtainPairSerializer,
    RoleSerializer,
    UserSerializer,
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


class UserViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = UserSerializer
    queryset = User.objects.all()  # metadata for DjangoModelPermissions; get_queryset() below does the real (tenant-scoped) filtering
    filterset_fields = ["department", "role", "is_active"]
    search_fields = ["email", "phone", "first_name", "last_name"]

    def get_queryset(self):
        user = self.request.user
        if user.is_staff:
            return User.objects.all()
        return User.objects.filter(hospital_id=user.hospital_id)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def me(self, request):
        return Response(UserSerializer(request.user).data)

    @action(detail=False, methods=["post"], url_path="switch-hospital", permission_classes=[IsAuthenticated])
    def switch_hospital(self, request):
        """Staff-only. This used to accept any authenticated user and
        reassign their `hospital` FK to *any* active hospital's id with no
        further check — since Hospital has no group/ownership concept in
        the schema, that meant any front-desk user at any hospital could
        call this with an arbitrary hospital_id and permanently switch
        themselves into a completely unrelated hospital's tenant, gaining
        full read/write access to its data through every other endpoint
        (verified empirically, not theoretical). Restricting to is_staff
        matches the only other cross-hospital mechanism already in this
        codebase (the X-Hospital-Id header in TenantMiddleware)."""
        if not request.user.is_staff:
            return Response({"detail": "Only staff may switch hospitals."}, status=status.HTTP_403_FORBIDDEN)

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
        return Response(UserSerializer(request.user).data)


    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated])
    def change_password(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        if not user.check_password(serializer.validated_data["old_password"]):
            return Response({"old_password": "Incorrect password."}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])
        update_session_auth_hash(request, user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class RoleViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = RoleSerializer
    queryset = Role.objects.all()  # metadata for DjangoModelPermissions; get_queryset() below does the real (tenant-scoped) filtering
    filterset_fields = ["department"]

    def get_queryset(self):
        user = self.request.user
        if user.is_staff:
            return Role.objects.all()
        return Role.objects.filter(hospital_id=user.hospital_id)
