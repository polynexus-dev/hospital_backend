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
    serializer_class = HospitalScopedTokenObtainPairSerializer


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
