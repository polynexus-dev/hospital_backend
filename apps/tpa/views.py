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
    # select_related: PreAuthRequestSerializer's patient_name/tpa_name
    # (source="patient.full_name"/"tpa_company.name")
    queryset = PreAuthRequest.objects.select_related("patient", "tpa_company")
    filterset_fields = ["tpa_company", "status", "patient"]
    search_fields = ["policy_number"]


class ClaimViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = ClaimSerializer
    # select_related: ClaimSerializer's patient_name/tpa_name
    # (source="patient.full_name"/"tpa_company.name")
    queryset = Claim.objects.select_related("patient", "tpa_company")
    filterset_fields = ["tpa_company", "status", "patient", "preauth_request"]
    search_fields = ["claim_number"]
