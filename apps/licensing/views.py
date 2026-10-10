from django.conf import settings
from rest_framework import status as http
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import service


def _is_installation_admin(user) -> bool:
    from apps.accounts.permission_catalog import is_hospital_admin

    return user.is_superuser or is_hospital_admin(user)


def _support():
    return {"email": settings.LICENSE_SUPPORT_EMAIL, "phone": settings.LICENSE_SUPPORT_PHONE}


class LicenseStatusView(APIView):
    """GET /licensing/status/ — `mode: "saas"` in SaaS mode; otherwise the
    license state (full details for hospital admins)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not service.is_on_premise():
            return Response({"mode": "saas"})
        data = service.current_status().as_dict(detail=_is_installation_admin(request.user))
        data["support"] = _support()
        return Response(data)


class LicenseUploadView(APIView):
    """POST /licensing/upload/ {"license": "<.lic file contents>"} — hospital admins only."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not service.is_on_premise():
            return Response({"detail": "Licenses apply to on-premise installations only."}, status=http.HTTP_400_BAD_REQUEST)
        if not _is_installation_admin(request.user):
            return Response({"detail": "Only a hospital admin can install a license."}, status=http.HTTP_403_FORBIDDEN)
        blob = request.data.get("license")
        if not isinstance(blob, str) or not blob.strip():
            return Response({"license": ["Upload the license file's contents."]}, status=http.HTTP_400_BAD_REQUEST)

        result = service.install_license(blob)
        if not result.usable:
            return Response({"detail": result.message, "state": result.state}, status=http.HTTP_400_BAD_REQUEST)

        from apps.governance import services as gov
        from apps.governance.models import SecurityEvent

        gov.log_security_event(
            SecurityEvent.EventType.POLICY_CHANGED, request=request, user=request.user,
            details={"action": "license_installed", "license_id": result.payload["license_id"], "expires_at": result.payload["expires_at"]},
        )
        return Response({**service.current_status().as_dict(detail=True), "support": _support()})
