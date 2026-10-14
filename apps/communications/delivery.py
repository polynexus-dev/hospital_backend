"""What happens to a message after it's sent: delivery reports, and people
asking us to stop.

- apply_delivery_status: a provider says a message was delivered / read /
  failed. Status only moves forward (sent -> delivered -> read), and the
  campaign the message belongs to gets its counts refreshed.
- MSG91DeliveryWebhookView: MSG91's "On Report Received" webhook (both the
  current flat payload and the legacy nested `report` list).
- UnsubscribeView: the link in every marketing email (also the one-click
  List-Unsubscribe POST mail apps send).
- handle_stop_reply: an inbound "STOP" on WhatsApp / SMS.
"""
import hmac
import logging

from django.conf import settings
from django.core import signing
from django.http import HttpResponse
from django.utils import timezone
from django.utils.html import escape
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import BroadcastCampaign, Channel, Message
from .services import UNSUBSCRIBE_SALT, contact_hash, suppress

logger = logging.getLogger(__name__)

RANK = {Message.Status.QUEUED: 0, Message.Status.SENT: 1, Message.Status.DELIVERED: 2, Message.Status.READ: 3}
STOP_WORDS = {"STOP", "UNSUBSCRIBE", "STOP ALL", "OPT OUT", "OPTOUT", "बंद", "रोको", "थांबवा"}


def refresh_campaign_counts(campaign_id):
    from django.db.models import Count, Q

    S = Message.Status
    counts = Message.objects.filter(campaign_id=campaign_id).aggregate(
        sent=Count("id", filter=Q(status__in=[S.SENT, S.DELIVERED, S.READ])),
        delivered=Count("id", filter=Q(status__in=[S.DELIVERED, S.READ])),
        read=Count("id", filter=Q(status=S.READ)),
        failed=Count("id", filter=Q(status=S.FAILED)),
    )
    BroadcastCampaign.objects.filter(pk=campaign_id).update(sent_count=counts["sent"], delivered_count=counts["delivered"],
                                                            read_count=counts["read"], failed_count=counts["failed"])


def apply_delivery_status(provider_message_id, status, *, error="", at=None):
    """Returns how many messages changed."""
    if not provider_message_id or status not in (Message.Status.DELIVERED, Message.Status.READ, Message.Status.FAILED):
        return 0
    at = at or timezone.now()
    changed = 0
    for msg in Message.objects.filter(provider_message_id=provider_message_id, direction=Message.Direction.OUTBOUND):
        if status == Message.Status.FAILED:
            if msg.status in (Message.Status.DELIVERED, Message.Status.READ, Message.Status.FAILED):
                continue
            msg.status, msg.error = Message.Status.FAILED, (error or "Not delivered")[:255]
        elif RANK.get(status, 0) > RANK.get(msg.status, -1):
            msg.status = status
            msg.delivered_at = msg.delivered_at or at
            if status == Message.Status.READ:
                msg.read_at = msg.read_at or at
        else:
            continue
        msg.save(update_fields=["status", "error", "delivered_at", "read_at", "updated_at"])
        changed += 1
        if msg.campaign_id:
            refresh_campaign_counts(msg.campaign_id)
    return changed


# --- MSG91 -------------------------------------------------------------------

_MSG91_DELIVERED = {"1", "delivered", "delivrd"}
_MSG91_FAILED = {"2", "failed", "16", "rejected", "17", "blocked", "9", "ndnc", "25", "26", "undelivered", "expired"}


def _msg91_status(value):
    v = str(value or "").strip().lower()
    if v in _MSG91_DELIVERED:
        return Message.Status.DELIVERED
    if v in _MSG91_FAILED:
        return Message.Status.FAILED
    return None


def parse_msg91_reports(payload):
    """Yields (request_id, status, reason) from either MSG91 webhook shape."""
    items = payload if isinstance(payload, list) else [payload]
    for item in items:
        if not isinstance(item, dict):
            continue
        request_id = str(item.get("requestId") or item.get("request_id") or "")
        reports = item.get("report") if isinstance(item.get("report"), list) else [item]
        for report in reports:
            if not isinstance(report, dict):
                continue
            status = _msg91_status(report.get("status")) or _msg91_status(report.get("desc"))
            if request_id and status:
                yield request_id, status, str(report.get("desc") or report.get("failureReason") or "")


class MSG91DeliveryWebhookView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request, token):
        expected = settings.MSG91_WEBHOOK_TOKEN
        if not expected or not hmac.compare_digest(str(token), expected):
            return Response({"detail": "Invalid token."}, status=403)
        updated = sum(apply_delivery_status(rid, status, error=reason) for rid, status, reason in parse_msg91_reports(request.data))
        return Response({"updated": updated})


# --- Unsubscribe ------------------------------------------------------------

_PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title></head><body style="font-family:Arial,sans-serif;max-width:520px;margin:60px auto;padding:0 16px;color:#222">
<h2>{title}</h2><p>{text}</p></body></html>"""


class UnsubscribeView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def _apply(self, token):
        try:
            data = signing.loads(token, salt=UNSUBSCRIBE_SALT)
        except signing.BadSignature:
            return None
        suppress(data["h"], data["c"], data["a"], source="email_link")
        return data

    def get(self, request, token):
        # Mail scanners prefetch links, so GET only asks; the button POSTs.
        try:
            signing.loads(token, salt=UNSUBSCRIBE_SALT)
        except signing.BadSignature:
            return HttpResponse(_PAGE.format(title="Link not valid", text="This unsubscribe link is broken or has been changed."), status=400)
        form = f'<form method="post"><button style="padding:10px 18px;font-size:15px">Unsubscribe me</button></form>'
        return HttpResponse(_PAGE.format(title="Stop these emails?", text=f"We'll stop sending you offers and updates by email. {form}"))

    def post(self, request, token):
        if self._apply(token) is None:
            return HttpResponse(_PAGE.format(title="Link not valid", text="This unsubscribe link is broken or has been changed."), status=400)
        return HttpResponse(_PAGE.format(title="You're unsubscribed",
                                         text=escape("You won't get marketing emails from us any more. Appointment and report messages still reach you.")))


def handle_stop_reply(hospital_id, channel, address, body):
    """True if the message was an opt-out request (and was recorded)."""
    if channel not in (Channel.WHATSAPP, Channel.SMS) or str(body or "").strip().upper().rstrip(".!") not in STOP_WORDS:
        return False
    suppress(hospital_id, channel, contact_hash(channel, address), source="stop_reply")
    return True
