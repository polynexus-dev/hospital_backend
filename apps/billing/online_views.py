"""Online payment links, the hospital's gateway settings and the Razorpay
webhook (NABH FPM.2.b, FPM.3.e). See online_payments.py."""
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import ActionPermissionRequired
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin

from . import online_payments as gw
from .models import Bill, OnlinePaymentLink, PaymentGatewayAccount

GATEWAY_ADMINS = {"owner", "admin", "hospital_administrator", "finance_manager"}


class OnlinePaymentLinkSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="bill.patient.full_name", read_only=True)

    class Meta:
        model = OnlinePaymentLink
        fields = ["id", "bill", "patient_name", "amount", "provider", "short_url", "status", "channel", "created_by", "payment", "paid_at",
                  "patient_notified_at", "created_at"]
        read_only_fields = fields


class OnlinePaymentLinkViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                               mixins.CreateModelMixin, viewsets.GenericViewSet):
    """POST {bill, amount?, notify?} creates a link for the bill's balance
    (or part of it) and sends it to the patient."""

    permission_classes = [permissions.IsAuthenticated, ActionPermissionRequired]
    action_permissions = {"list": "billing.view_payment", "retrieve": "billing.view_payment", "create": "billing.add_payment", "cancel": "billing.add_payment"}
    serializer_class = OnlinePaymentLinkSerializer
    queryset = OnlinePaymentLink.objects.select_related("bill__patient")
    filterset_fields = ["bill", "status"]

    def create(self, request, *args, **kwargs):
        bill = Bill.objects.filter(pk=request.data.get("bill"), hospital_id=request.user.hospital_id).select_related("patient", "hospital").first()
        if bill is None:
            return Response({"bill": "Bill not found."}, status=404)
        try:
            link = gw.create_payment_link(bill, amount=request.data.get("amount"), user=request.user, notify=request.data.get("notify", True) not in (False, "false", "0"))
        except gw.GatewayNotConfigured as exc:
            return Response({"detail": str(exc), "code": "gateway_not_configured"}, status=503)
        except gw.GatewayError as exc:
            return Response({"detail": str(exc)}, status=400)
        self._log("create", link)
        return Response(self.get_serializer(link).data, status=201)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        link = self.get_object()
        try:
            gw.cancel_payment_link(link)
        except gw.GatewayError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(self.get_serializer(link).data)


class PaymentGatewayView(APIView):
    """GET status / PUT {key_id, key_secret, webhook_secret, is_active}.
    Secrets are write-only: they are never sent back."""

    permission_classes = [permissions.IsAuthenticated]

    def _allowed(self, user):
        return user.is_superuser or getattr(getattr(user, "role", None), "template", "") in GATEWAY_ADMINS

    def _status(self, request):
        acct = PaymentGatewayAccount.objects.filter(hospital_id=request.user.hospital_id).first()
        try:
            gw.credentials_for(request.user.hospital_id)
            configured = True
        except gw.GatewayNotConfigured:
            configured = False
        return {
            "configured": configured,
            "source": "hospital" if acct and acct.is_active else ("server" if configured else None),
            "provider": "razorpay", "key_id": acct.key_id if acct else "", "is_active": bool(acct and acct.is_active),
            "webhook_url": request.build_absolute_uri(f"/api/v1/billing/payment-webhook/razorpay/{request.user.hospital_id}/"),
            "webhook_events": ["payment_link.paid", "payment_link.expired", "payment_link.cancelled"],
        }

    def get(self, request):
        if not self._allowed(request.user):
            return Response({"detail": "Hospital administrators only."}, status=403)
        return Response(self._status(request))

    def put(self, request):
        if not self._allowed(request.user):
            return Response({"detail": "Hospital administrators only."}, status=403)
        acct = PaymentGatewayAccount.objects.filter(hospital_id=request.user.hospital_id).first() or PaymentGatewayAccount(hospital_id=request.user.hospital_id)
        key_id = str(request.data.get("key_id", acct.key_id or "")).strip()
        if not key_id.startswith(("rzp_live_", "rzp_test_")):
            return Response({"key_id": "A Razorpay key id starts with rzp_live_ or rzp_test_."}, status=400)
        acct.key_id = key_id
        for field in ("key_secret", "webhook_secret"):
            if request.data.get(field):
                setattr(acct, field, str(request.data[field]).strip())
            elif not getattr(acct, field):
                return Response({field: "Required."}, status=400)
        acct.is_active = request.data.get("is_active", True) not in (False, "false", "0")
        acct.save()
        from apps.core.audit import log_action

        log_action(actor=request.user, action="update", instance=acct, changes={"key_id": key_id, "is_active": acct.is_active, "secrets": "[redacted]"}, request=request, force=True)
        return Response(self._status(request))


@method_decorator(csrf_exempt, name="dispatch")
class RazorpayWebhookView(APIView):
    """Razorpay calls this; the signature (HMAC of the raw body with the
    hospital's webhook secret) is the only authentication."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request, hospital_id):
        code, message = gw.handle_webhook(hospital_id, request.body, request.headers.get("X-Razorpay-Signature", ""))
        return Response({"status": message}, status=code)
