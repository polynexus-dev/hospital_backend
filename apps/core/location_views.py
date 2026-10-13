"""Address lookups for registration: states, districts, and PIN code →
district, sub-district and areas (India Post directory). Read-only reference
data, open to any signed-in user."""
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import IndianState, PinCode


class StateListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response([{"name": s.name, "is_union_territory": s.is_union_territory} for s in IndianState.objects.all()])


class DistrictListView(APIView):
    """GET ?state=Maharashtra → districts known from the PIN directory."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        state = request.query_params.get("state", "")
        districts = PinCode.objects.filter(state__iexact=state).values_list("district", flat=True).distinct().order_by("district")
        return Response(list(districts))


class PinCodeLookupView(APIView):
    """GET /locations/pincode/411038/ → {"found", "state", "district", "sub_districts", "areas"}."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pincode):
        offices = list(PinCode.objects.filter(pincode=pincode))
        if not offices:
            return Response({"pincode": pincode, "found": False, "directory_loaded": PinCode.objects.exists()})

        def most_common(values):
            values = [v for v in values if v]
            return max(set(values), key=values.count) if values else ""

        return Response({
            "pincode": pincode,
            "found": True,
            "state": most_common([o.state for o in offices]),
            "district": most_common([o.district for o in offices]),
            "sub_districts": sorted({o.sub_district for o in offices if o.sub_district}),
            "areas": sorted({o.office_name for o in offices}),
        })


class CanRefreshPinDirectory(BasePermission):
    """The directory is shared by every hospital on the server: Polynexus
    staff refresh it in SaaS; on-premise, the hospital's administrators."""

    def has_permission(self, request, view):
        from apps.accounts.permission_catalog import is_hospital_admin
        from apps.licensing.service import is_on_premise

        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.is_superuser or getattr(user, "can_cross_tenant", False):
            return True
        return is_on_premise() and is_hospital_admin(user)


class PinDirectoryRefreshView(APIView):
    """GET: directory size and the last refresh. POST: refresh from data.gov.in in the background."""

    permission_classes = [IsAuthenticated, CanRefreshPinDirectory]

    def _status(self):
        from django.conf import settings

        from .models import PinDirectoryRefresh

        last = PinDirectoryRefresh.objects.first()
        return {
            "pincodes": PinCode.objects.values("pincode").distinct().count(),
            "post_offices": PinCode.objects.count(),
            "api_key_configured": bool(getattr(settings, "DATA_GOV_IN_API_KEY", "")),
            "last_refresh": None if last is None else {
                "started_at": last.started_at, "finished_at": last.finished_at, "running": last.finished_at is None,
                "added_pincodes": None if last.pincodes_after is None else last.pincodes_after - last.pincodes_before,
                "added_post_offices": None if last.offices_after is None else last.offices_after - last.offices_before,
                "error": last.error,
            },
        }

    def get(self, request):
        return Response(self._status())

    def post(self, request):
        from datetime import timedelta

        from django.conf import settings
        from django.utils import timezone
        from rest_framework import status

        from .models import PinDirectoryRefresh
        from .tasks import refresh_pin_directory

        if not getattr(settings, "DATA_GOV_IN_API_KEY", ""):
            return Response({"detail": "Set DATA_GOV_IN_API_KEY in the server's .env first."}, status=status.HTTP_400_BAD_REQUEST)
        running = PinDirectoryRefresh.objects.filter(finished_at__isnull=True, started_at__gt=timezone.now() - timedelta(hours=1)).exists()
        if running:
            return Response({"detail": "A refresh is already running."}, status=status.HTTP_409_CONFLICT)
        run = PinDirectoryRefresh.objects.create(
            started_by=request.user, pincodes_before=PinCode.objects.values("pincode").distinct().count(), offices_before=PinCode.objects.count(),
        )
        refresh_pin_directory.delay(run.pk)
        return Response(self._status(), status=status.HTTP_202_ACCEPTED)
