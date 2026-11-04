"""
Endpoints ABDM's gateway calls (the "bridge" URL registered for this
facility is https://<host>/api/v1/abdm/callbacks; the gateway appends the
/api/v3/... paths below).

Each call must carry a JWT signed by the gateway (checked against its
published keys) and a REQUEST-ID. We store the call — payload encrypted —
answer 202 at once, and process it in a Celery task: ABDM expects quick
acknowledgements and sends the real answer to its own on-* endpoints.
A repeated REQUEST-ID is acknowledged and not processed twice.

The data push is different: the HIP posts it straight to us (no gateway
token), so it is accepted only for a transfer we started, and only content
that decrypts with that transfer's private key is stored (apps.abdm.hiu).
"""
import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from . import hip, hiu
from .gateway import GatewayAuthError, verify_gateway_token
from .models import ABDMFacility, GatewayCallback

logger = logging.getLogger(__name__)

# path -> (handler, which header names the facility)
HANDLERS = {
    "hip/patient/care-context/discover": (hip.handle_discover, "X-HIP-ID"),
    "hip/link/care-context/init": (hip.handle_link_init, "X-HIP-ID"),
    "hip/link/care-context/confirm": (hip.handle_link_confirm, "X-HIP-ID"),
    "hip/token/on-generate-token": (hip.handle_link_token, "X-HIP-ID"),
    "link/on_carecontext": (hip.handle_link_on_carecontext, "X-HIP-ID"),
    "consent/request/hip/notify": (hip.handle_consent_notify, "X-HIP-ID"),
    "hip/health-information/request": (hip.handle_hi_request, "X-HIP-ID"),
    "hiu/consent/request/on-init": (hiu.handle_consent_on_init, "X-HIU-ID"),
    "hiu/consent/request/notify": (hiu.handle_consent_notify, "X-HIU-ID"),
    "hiu/consent/on-fetch": (hiu.handle_consent_on_fetch, "X-HIU-ID"),
    "hiu/health-information/on-request": (hiu.handle_hi_on_request, "X-HIU-ID"),
}


def facility_for(header_value):
    """The facility a callback is for. A single-hospital install may set
    ABDM_HIP_ID instead of creating an ABDMFacility row."""
    facility = ABDMFacility.objects.select_related("hospital").filter(hip_id=header_value).first() or \
        ABDMFacility.objects.select_related("hospital").filter(hiu_id=header_value).exclude(hiu_id="").first()
    if facility is None and header_value and header_value == settings.ABDM_HIP_ID:
        from apps.core.models import Hospital

        hospitals = list(Hospital.objects.filter(is_active=True)[:2])
        if len(hospitals) == 1:
            facility, _ = ABDMFacility.objects.get_or_create(hospital=hospitals[0], defaults={"hip_id": header_value})
    return facility


class GatewayCallbackView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "abdm_gateway"

    def post(self, request, path):
        handler = HANDLERS.get(path.strip("/"))
        if handler is None:
            return Response({"detail": "Unknown callback."}, status=status.HTTP_404_NOT_FOUND)
        try:
            verify_gateway_token(request.headers.get("Authorization", ""))
        except GatewayAuthError as exc:
            logger.warning("Rejected ABDM callback %s: %s", path, exc)
            return Response({"detail": "Gateway authentication failed."}, status=status.HTTP_401_UNAUTHORIZED)
        request_id = request.headers.get("REQUEST-ID", "").strip()
        if not request_id:
            return Response({"detail": "REQUEST-ID header is required."}, status=status.HTTP_400_BAD_REQUEST)
        facility = facility_for(request.headers.get(handler[1], ""))
        if facility is None:
            return Response({"detail": f"Unknown {handler[1]}."}, status=status.HTTP_404_NOT_FOUND)
        try:
            with transaction.atomic():
                callback = GatewayCallback.objects.create(request_id=request_id[:100], path=path.strip("/"), hospital=facility.hospital, payload=request.data)
        except IntegrityError:
            return Response(status=status.HTTP_202_ACCEPTED)  # a retry of a call we already have
        from .tasks import process_gateway_callback

        transaction.on_commit(lambda: process_gateway_callback.delay(callback.pk))
        return Response(status=status.HTTP_202_ACCEPTED)


def process(callback_id):
    """Runs one stored callback (Celery). The payload is dropped afterwards."""
    from apps.core.modules import is_enabled

    callback = GatewayCallback.objects.select_related("hospital").get(pk=callback_id)
    if callback.processed_at:
        return
    facility = ABDMFacility.objects.select_related("hospital").get(hospital=callback.hospital)
    handler = HANDLERS[callback.path][0]
    try:
        if not is_enabled(facility.hospital, "abdm"):
            raise PermissionError("The ABDM module isn't enabled for this hospital.")
        result = handler(callback, callback.payload, facility)
        if callback.path == "hip/health-information/request" and result is not None:
            hip.send_health_information(result, facility)
    except Exception as exc:
        logger.exception("ABDM callback %s (%s) failed", callback.path, callback.request_id)
        callback.error = str(exc)[:500]
    callback.processed_at = timezone.now()
    callback.payload = {}
    callback.save(update_fields=["processed_at", "error", "payload"])


class DataPushView(APIView):
    """POST /api/v1/abdm/data-push/ — encrypted records from a HIP (M3)."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "abdm_gateway"

    def post(self, request):
        try:
            hiu.receive_push(request.data)
        except hiu.DataPushRejected as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_202_ACCEPTED)
