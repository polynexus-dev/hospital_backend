from rest_framework import viewsets
from apps.core.viewsets import TenantScopedViewSetMixin

from .models import PreAuthRequest, TPACompany
from .serializers import PreAuthRequestSerializer, TPACompanySerializer


class TPACompanyViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = TPACompanySerializer
    queryset = TPACompany.objects.all()
    filterset_fields = ["is_active"]
    search_fields = ["name", "code"]


class PreAuthRequestViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = PreAuthRequestSerializer
    queryset = PreAuthRequest.objects.all()
    filterset_fields = ["tpa_company", "status", "patient"]
    search_fields = ["policy_number"]
