from django.db.models import Count, Sum
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.viewsets import TenantScopedViewSetMixin

from .models import FieldVisit, ReferralRecord, ReferringDoctor
from .serializers import (
    FieldVisitSerializer,
    ReferralRecordSerializer,
    ReferringDoctorSerializer,
)


class ReferringDoctorViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = ReferringDoctorSerializer
    queryset = ReferringDoctor.objects.all()
    filterset_fields = ["tier", "is_active", "city"]
    search_fields = ["name", "clinic_name", "mobile", "speciality"]

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.annotate(
            total_referrals=Count("referrals"),
            total_attributed_revenue=Sum("referrals__attributed_revenue"),
        )

    @action(detail=False, methods=["get"], url_path="league-table")
    def league_table(self, request):
        """Returns top referring doctors ranked by revenue attribution."""
        top_doctors = self.get_queryset().order_by("-total_attributed_revenue")[:10]
        return Response(ReferringDoctorSerializer(top_doctors, many=True).data)


class ReferralRecordViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = ReferralRecordSerializer
    queryset = ReferralRecord.objects.all()
    filterset_fields = ["referring_doctor", "patient", "status", "department"]


class FieldVisitViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = FieldVisitSerializer
    queryset = FieldVisit.objects.all()
    filterset_fields = ["referring_doctor", "visited_by"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, visited_by=self.request.user)
