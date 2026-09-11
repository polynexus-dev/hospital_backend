from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import RequiresViewPermission, RoleBasedModelPermissions
from apps.core.viewsets import TenantScopedViewSetMixin

from .gateway import GatewayNotConfigured, get_abdm_gateway, get_nhcx_gateway
from .models import AbhaLink, ConsentRequest, HealthRecordFetch, NHCXTransaction
from .serializers import (
    AbhaLinkSerializer,
    ConsentRequestSerializer,
    HealthRecordFetchSerializer,
    NHCXTransactionSerializer,
    VerifyAbhaOtpSerializer,
)


class GatewayUnavailable(APIException):
    """Raised from perform_create when the gateway (still just
    StubABDMGateway/StubNHCXGateway — see gateway.py) can't actually do
    anything yet, so DRF returns a clean 503 instead of either a
    fabricated success or an unhandled 500."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "This integration is not connected yet."


# ABHA numbers/addresses are a national health identifier — same
# reasoning as apps.privacy's PRIVACY_PERMISSION_CLASSES: reads need
# RequiresViewPermission on top of RoleBasedModelPermissions, not just
# IsAuthenticated, since RoleBasedModelPermissions alone never gates
# list/retrieve (see apps.core.permissions.RequiresViewPermission's
# docstring). apps.accounts.permission_templates decides who actually
# holds "abdm" at all.
ABDM_PERMISSION_CLASSES = [IsAuthenticated, RoleBasedModelPermissions, RequiresViewPermission]


class AbhaLinkViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = AbhaLinkSerializer
    queryset = AbhaLink.objects.all()
    permission_classes = ABDM_PERMISSION_CLASSES
    filterset_fields = ["patient", "status"]

    def perform_create(self, serializer):
        identifier = serializer.validated_data.pop("identifier")
        hospital = getattr(self.request.user, "hospital", None)
        method = serializer.validated_data["verification_method"]

        try:
            result = get_abdm_gateway().initiate_abha_verification(identifier=identifier, method=method)
        except GatewayNotConfigured as exc:
            # Nothing persisted — a failed initiation shouldn't leave a
            # PENDING row with no way to ever complete it.
            raise GatewayUnavailable(str(exc)) from exc

        serializer.save(hospital=hospital, initiated_by=self.request.user, gateway_txn_id=result["txn_id"])

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        link = self.get_object()
        serializer = VerifyAbhaOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = get_abdm_gateway().verify_abha_otp(txn_id=link.gateway_txn_id, otp=serializer.validated_data["otp"])
        except GatewayNotConfigured as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        link.status = AbhaLink.Status.LINKED
        link.abha_number = result["abha_number"]
        link.abha_address = result["abha_address"]
        link.linked_at = timezone.now()
        link.save(update_fields=["status", "abha_number", "abha_address", "linked_at"])
        return Response(AbhaLinkSerializer(link).data)


class ConsentRequestViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = ConsentRequestSerializer
    queryset = ConsentRequest.objects.all()
    permission_classes = ABDM_PERMISSION_CLASSES
    filterset_fields = ["patient", "status"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        data = serializer.validated_data

        try:
            result = get_abdm_gateway().create_consent_request(
                abha_address=data["abha_link"].abha_address,
                purpose=data["purpose"],
                hi_types=data.get("hi_types", []),
                date_from=data["date_range_from"],
                date_to=data["date_range_to"],
            )
        except GatewayNotConfigured as exc:
            raise GatewayUnavailable(str(exc)) from exc

        serializer.save(hospital=hospital, requested_by=self.request.user, gateway_request_id=result["gateway_request_id"])

    @action(detail=True, methods=["post"])
    def check_status(self, request, pk=None):
        """Polls the gateway for a status change — ABDM's consent grant/
        deny happens out-of-band (the patient acts in their Consent
        Manager app), so this app only ever finds out by asking."""
        consent_request = self.get_object()
        try:
            result = get_abdm_gateway().fetch_consent_status(gateway_request_id=consent_request.gateway_request_id)
        except GatewayNotConfigured as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        consent_request.status = result["status"]
        consent_request.consent_artifact_id = result.get("consent_artifact_id") or ""
        consent_request.expires_at = result.get("expires_at")
        if result["status"] in (ConsentRequest.Status.GRANTED, ConsentRequest.Status.DENIED) and not consent_request.responded_at:
            consent_request.responded_at = timezone.now()
        consent_request.save(update_fields=["status", "consent_artifact_id", "expires_at", "responded_at"])
        return Response(ConsentRequestSerializer(consent_request).data)

    @action(detail=True, methods=["post"])
    def fetch_records(self, request, pk=None):
        """The actual health-record retrieval — only once GRANTED, so an
        expired/denied/still-pending request can't be used to pull data
        that was never actually authorized."""
        consent_request = self.get_object()
        if consent_request.status != ConsentRequest.Status.GRANTED:
            return Response({"detail": "Consent has not been granted yet."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            bundle = get_abdm_gateway().fetch_health_records(consent_artifact_id=consent_request.consent_artifact_id)
        except GatewayNotConfigured as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        HealthRecordFetch.objects.create(
            hospital=getattr(request.user, "hospital", None),
            consent_request=consent_request,
            fetched_by=request.user,
            hi_types_fetched=consent_request.hi_types,
            record_count=len(bundle.get("entry", [])) if isinstance(bundle, dict) else 0,
        )
        return Response(bundle)


class HealthRecordFetchViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Read-only audit trail of actual retrievals — see HealthRecordFetch's
    docstring. Nothing ever writes to this except fetch_records above."""

    serializer_class = HealthRecordFetchSerializer
    queryset = HealthRecordFetch.objects.all()
    permission_classes = ABDM_PERMISSION_CLASSES
    filterset_fields = ["consent_request"]


class NHCXTransactionViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = NHCXTransactionSerializer
    queryset = NHCXTransaction.objects.all()
    permission_classes = ABDM_PERMISSION_CLASSES
    filterset_fields = ["preauth_request", "transaction_type", "status"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        data = serializer.validated_data
        preauth_request = data.get("preauth_request")

        try:
            result = get_nhcx_gateway().submit_transaction(
                transaction_type=data["transaction_type"],
                payload={"preauth_request_id": preauth_request.id if preauth_request else None},
            )
        except GatewayNotConfigured as exc:
            raise GatewayUnavailable(str(exc)) from exc

        serializer.save(
            hospital=hospital, initiated_by=self.request.user,
            nhcx_transaction_id=result["nhcx_transaction_id"], status=result.get("status", NHCXTransaction.Status.INITIATED),
        )

    @action(detail=True, methods=["post"])
    def check_status(self, request, pk=None):
        transaction = self.get_object()
        try:
            result = get_nhcx_gateway().fetch_transaction_status(nhcx_transaction_id=transaction.nhcx_transaction_id)
        except GatewayNotConfigured as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        transaction.status = result["status"]
        transaction.gateway_response_summary = result.get("summary", "")
        transaction.save(update_fields=["status", "gateway_response_summary", "updated_at"])
        return Response(NHCXTransactionSerializer(transaction).data)
