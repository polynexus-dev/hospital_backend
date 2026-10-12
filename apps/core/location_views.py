"""Address lookups for registration: states, districts, and PIN code →
district, sub-district and areas (India Post directory). Read-only reference
data, open to any signed-in user."""
from rest_framework.permissions import IsAuthenticated
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
