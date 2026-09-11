from rest_framework import viewsets
from apps.core.viewsets import TenantScopedViewSetMixin

from .models import Claim, PreAuthRequest, TPACompany
from .serializers import ClaimSerializer, PreAuthRequestSerializer, TPACompanySerializer


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


class ClaimViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = ClaimSerializer
    queryset = Claim.objects.all()
    filterset_fields = ["tpa_company", "status", "patient", "preauth_request"]
    search_fields = ["claim_number"]
