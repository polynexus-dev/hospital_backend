from django.db.models import Count, Sum
from rest_framework import viewsets
from apps.core.viewsets import TenantScopedViewSetMixin

from .models import CampRegistration, Campaign, HealthPackage
from .serializers import (
    CampRegistrationSerializer,
    CampaignSerializer,
    HealthPackageSerializer,
)


class HealthPackageViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = HealthPackageSerializer
    queryset = HealthPackage.objects.all()
    filterset_fields = ["category", "is_active"]
    search_fields = ["name", "code"]


class CampaignViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = CampaignSerializer
    queryset = Campaign.objects.all()
    filterset_fields = ["campaign_type", "status"]
    search_fields = ["name"]

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.annotate(
            total_registrations=Count("registrations"),
            total_revenue_generated=Sum("registrations__revenue_generated"),
        )


class CampRegistrationViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = CampRegistrationSerializer
    queryset = CampRegistration.objects.all()
    filterset_fields = ["campaign", "stage"]
    search_fields = ["patient_name", "mobile"]
