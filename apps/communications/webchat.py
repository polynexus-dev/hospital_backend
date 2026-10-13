"""Website live chat: the chat bubble of the website widget
(apps.enquiries.website) and the staff side of it in the Inbox.

A visitor starts a chat with their name, mobile and consent; that creates
(or reuses, through duplicate detection) a Website enquiry and a `web_chat`
thread. The visitor's browser then holds a signed chat token and polls for
replies; staff answer with free text from Inbox -> Website chats.
"""
import json
import time

from django.core import signing
from django.utils import timezone
from rest_framework import permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.modules import require_module
from apps.enquiries.models import Enquiry, WebsiteWidget
from apps.enquiries.website import MOBILE, MIN_FILL_SECONDS, _client_ip, _cors, _origin_ok, _over_limit, public_config
from apps.core.encryption import normalize_phone

from .models import Channel, Message, Thread

TOKEN_SALT = "webchat"
TOKEN_MAX_AGE = 60 * 60 * 24 * 30   # a visitor can come back to the same chat for 30 days
MESSAGE_LIMIT = (30, 600)           # 30 visitor messages per 10 minutes
INBOUND_SLA_MINUTES = 5             # a website visitor expects a quick answer


def _widget(site_key):
    w = WebsiteWidget.objects.filter(site_key=site_key, chat_enabled=True, hospital__is_active=True).select_related("hospital").first()
    if w is not None:
        require_module(w.hospital, "inbox")
    return w


def _thread_from_token(widget, token):
    try:
        data = signing.loads(token or "", salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
    except signing.BadSignature:
        return None
    if data.get("w") != widget.pk:
        return None
    return Thread.objects.filter(pk=data.get("t"), hospital=widget.hospital, channel=Channel.WEB_CHAT).select_related("enquiry").first()


def _record(thread, body, direction, *, sent_by=None, read=False):
    msg = Message.objects.create(
        hospital=thread.hospital, enquiry=thread.enquiry, patient=thread.patient, channel=Channel.WEB_CHAT, direction=direction,
        body=body, sent_by=sent_by, is_read=read,
        status=Message.Status.RECEIVED if direction == Message.Direction.INBOUND else Message.Status.SENT,
    )
    now = timezone.now()
    thread.last_message_at = now
    if direction == Message.Direction.INBOUND:
        thread.last_inbound_at = now
        thread.sla_due_at = now + timezone.timedelta(minutes=INBOUND_SLA_MINUTES)
        thread.status = Thread.Status.OPEN
    elif sent_by is not None:
        thread.sla_due_at = None
    thread.save(update_fields=["last_message_at", "last_inbound_at", "sla_due_at", "status"])
    return msg


def serialize(msg):
    who = "visitor" if msg.direction == Message.Direction.INBOUND else ("staff" if msg.sent_by_id else "auto")
    name = msg.sent_by.first_name if msg.sent_by_id and msg.sent_by.first_name else ""
    return {"id": msg.pk, "from": who, "name": name, "body": msg.body, "at": msg.created_at.isoformat()}


def _messages(thread, after=0):
    qs = Message.objects.filter(hospital=thread.hospital, channel=Channel.WEB_CHAT, pk__gt=after).select_related("sent_by").order_by("pk")
    qs = qs.filter(enquiry=thread.enquiry) if thread.enquiry_id else qs.filter(patient=thread.patient)
    return [serialize(m) for m in qs[:200]]


class PublicView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def guard(self, request, site_key):
        w = _widget(site_key)
        if w is None:
            return None, Response({"detail": "Chat isn't available."}, status=404)
        origin = request.headers.get("Origin", "")
        # Browsers omit Origin on same-site GETs; polling is authorised by the signed chat token anyway.
        if (origin or request.method != "GET") and not (origin and _origin_ok(request, w, origin)):
            return None, Response({"detail": "This website isn't allowed to use this chat."}, status=403)
        return w, None

    def body(self, request):
        try:
            data = json.loads(request.body or b"{}")
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}


class ChatStartView(PublicView):
    """POST {name, mobile, message, consent, started_at, website, utm_*...}"""

    def post(self, request, site_key):
        w, err = self.guard(request, site_key)
        if err:
            return err
        data = self.body(request)
        started = data.get("started_at")
        if str(data.get("website") or "").strip() or (isinstance(started, (int, float)) and time.time() * 1000 - started < MIN_FILL_SECONDS * 1000):
            return _cors(Response({"detail": "Please try again in a moment."}, status=400), request, w)
        name = str(data.get("name") or "").strip()[:120]
        mobile = normalize_phone(str(data.get("mobile") or ""))
        text = str(data.get("message") or "").strip()[:2000]
        errors = {}
        if len(name) < 2:
            errors["name"] = "Please enter your name."
        if not MOBILE.match(mobile):
            errors["mobile"] = "Please enter a valid 10-digit mobile number."
        if not text:
            errors["message"] = "Please type your question."
        if data.get("consent") is not True:
            errors["consent"] = "Please tick the consent box so we can reply."
        if errors:
            return _cors(Response(errors, status=400), request, w)
        if _over_limit(f"webchat:start:{w.pk}:{_client_ip(request)}", (5, 3600)):
            return _cors(Response({"detail": "Too many chats from this connection. Please call us."}, status=429), request, w)

        consent = public_config(w)["consent_text"]
        enquiry = Enquiry.objects.create(
            hospital=w.hospital, name=name, mobile=mobile, source=Enquiry.Source.WEBSITE, service_requested="Website chat",
            notes=f"Started a website chat. Consent given at {timezone.localtime():%d %b %Y %H:%M}: \"{consent}\"",
            utm_source=str(data.get("utm_source") or "")[:100], utm_medium=str(data.get("utm_medium") or "")[:100],
            utm_campaign=str(data.get("utm_campaign") or "")[:150], campaign=str(data.get("utm_campaign") or "")[:150],
        )
        thread = Thread.objects.create(hospital=w.hospital, enquiry=enquiry, channel=Channel.WEB_CHAT)
        if w.chat_welcome:
            _record(thread, w.chat_welcome, Message.Direction.OUTBOUND, read=True)
        _record(thread, text, Message.Direction.INBOUND)
        if w.chat_auto_reply:
            _record(thread, w.chat_auto_reply, Message.Direction.OUTBOUND, read=True)
        token = signing.dumps({"t": thread.pk, "w": w.pk}, salt=TOKEN_SALT)
        return _cors(Response({"token": token, "messages": _messages(thread)}, status=201), request, w)


class ChatMessagesView(PublicView):
    """GET ?token=&after=<id> (poll)  ·  POST {token, body}"""

    def get(self, request, site_key):
        w, err = self.guard(request, site_key)
        if err:
            return err
        thread = _thread_from_token(w, request.query_params.get("token"))
        if thread is None:
            return _cors(Response({"detail": "Chat not found."}, status=404), request, w)
        try:
            after = int(request.query_params.get("after") or 0)
        except ValueError:
            after = 0
        return _cors(Response({"messages": _messages(thread, after), "closed": thread.status == Thread.Status.CLOSED}), request, w)

    def post(self, request, site_key):
        w, err = self.guard(request, site_key)
        if err:
            return err
        data = self.body(request)
        thread = _thread_from_token(w, data.get("token"))
        if thread is None:
            return _cors(Response({"detail": "Chat not found."}, status=404), request, w)
        text = str(data.get("body") or "").strip()[:2000]
        if not text:
            return _cors(Response({"body": "Type a message."}, status=400), request, w)
        if _over_limit(f"webchat:msg:{thread.pk}", MESSAGE_LIMIT):
            return _cors(Response({"detail": "You're sending messages too quickly."}, status=429), request, w)
        msg = _record(thread, text, Message.Direction.INBOUND)
        return _cors(Response({"message": serialize(msg)}, status=201), request, w)


class WebChatStaffMixin:
    """Added to ThreadViewSet: the staff side of website chats."""

    def _web_thread(self):
        from rest_framework.exceptions import PermissionDenied, ValidationError

        # Custom actions skip the model-permission check, so ask for Inbox access here.
        if not self.request.user.has_perm("communications.view_thread"):
            raise PermissionDenied("No Inbox access.")
        thread = self.get_object()
        if thread.channel != Channel.WEB_CHAT:
            raise ValidationError({"detail": "Free-text replies are only for website chats (WhatsApp/SMS use approved templates)."})
        return thread

    @action(detail=False, methods=["get"], url_path="web-chat-alerts")
    def web_chat_alerts(self, request):
        """Light poll for the app-wide new-chat alert: open website chats
        with unread visitor messages, newest first."""
        from django.db.models import Count, Q

        if not request.user.has_perm("communications.view_thread"):
            return Response({"detail": "No Inbox access."}, status=403)
        unread = Q(enquiry__messages__channel=Channel.WEB_CHAT, enquiry__messages__direction=Message.Direction.INBOUND, enquiry__messages__is_read=False)
        threads = (self.get_queryset().filter(channel=Channel.WEB_CHAT, status=Thread.Status.OPEN, enquiry__isnull=False)
                   .annotate(unread=Count("enquiry__messages", filter=unread)).filter(unread__gt=0)
                   .select_related("enquiry").order_by("-last_inbound_at")[:20])
        rows = []
        for t in threads:
            last = (Message.objects.filter(hospital=t.hospital, enquiry=t.enquiry, channel=Channel.WEB_CHAT, direction=Message.Direction.INBOUND)
                    .order_by("-pk").first())
            rows.append({"thread": t.pk, "name": t.enquiry.name, "unread": t.unread, "last_message_id": last.pk if last else 0,
                         "preview": (last.body if last else "")[:120], "at": t.last_inbound_at})
        return Response({"unread_chats": len(rows), "chats": rows})

    @action(detail=True, methods=["get"], url_path="chat-messages")
    def chat_messages(self, request, pk=None):
        thread = self._web_thread()
        Message.objects.filter(hospital=thread.hospital, enquiry=thread.enquiry, channel=Channel.WEB_CHAT, direction=Message.Direction.INBOUND, is_read=False).update(is_read=True)
        return Response({"messages": _messages(thread), "enquiry": {"id": thread.enquiry_id, "name": thread.enquiry.name if thread.enquiry_id else "",
                                                                   "mobile": thread.enquiry.mobile if thread.enquiry_id else ""}})

    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        thread = self._web_thread()
        text = str(request.data.get("body") or "").strip()[:2000]
        if not text:
            return Response({"body": "Type a reply."}, status=400)
        if thread.owner_id is None:
            thread.owner = request.user
            thread.save(update_fields=["owner"])
        msg = _record(thread, text, Message.Direction.OUTBOUND, sent_by=request.user, read=True)
        if thread.enquiry_id and thread.enquiry.stage == Enquiry.Stage.NEW:
            enquiry = thread.enquiry
            enquiry.stage = Enquiry.Stage.CONTACTED
            enquiry.save()  # through save(), so the stage history records who made contact
        return Response(serialize(msg), status=201)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        thread = self._web_thread()
        thread.status = Thread.Status.CLOSED
        thread.sla_due_at = None
        thread.save(update_fields=["status", "sla_due_at"])
        _record(thread, "This chat has been closed. Start a new chat any time.", Message.Direction.OUTBOUND, read=True)
        return Response({"status": "closed"})

