from rest_framework import viewsets
from apps.core.encryption import compute_blind_index
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
    # policy_number is encrypted at rest (apps.core.encryption) — DRF's
    # SearchFilter does an `icontains` against the raw column, which can
    # never match ciphertext, so it can't be a search_fields entry anymore.
    # ?policy_number=<value> below does an exact-match lookup instead, via
    # the deterministic policy_number_lookup companion column — see
    # PreAuthRequest.save() and docs/SECURITY_COMPLIANCE.md finding C2.

    def get_queryset(self):
        queryset = super().get_queryset()
        policy_number = self.request.query_params.get("policy_number")
        if policy_number:
            queryset = queryset.filter(policy_number_lookup=compute_blind_index(policy_number))
        return queryset


class ClaimViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = ClaimSerializer
    queryset = Claim.objects.all()
    filterset_fields = ["tpa_company", "status", "patient", "preauth_request"]
    search_fields = ["claim_number"]
