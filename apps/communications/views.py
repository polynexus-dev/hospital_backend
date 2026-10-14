from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from apps.licensing.permissions import HasFeature

from apps.core.viewsets import TenantScopedViewSetMixin
from apps.patients.models import Patient, record_timeline_event

from .models import BroadcastCampaign, ConsentOptOut, Message, Template, Thread
from .serializers import (
    BroadcastCampaignSerializer,
    ConsentOptOutSerializer,
    MessageSerializer,
    SendMessageSerializer,
    TemplateSerializer,
    ThreadSerializer,
)
from .services import send_message, touch_thread


from .webchat import WebChatStaffMixin

class TemplateViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = TemplateSerializer
    queryset = Template.objects.all()
    filterset_fields = ["channel", "language", "purpose", "is_active"]


class ConsentOptOutViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = ConsentOptOutSerializer
    queryset = ConsentOptOut.objects.all()
    filterset_fields = ["patient", "channel", "purpose", "is_opted_out"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, recorded_by=self.request.user)


class MessageViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Read-only unified inbox — outbound sends go through `send`, inbound
    messages arrive via the per-channel webhook views below."""

    serializer_class = MessageSerializer
    queryset = Message.objects.all()
    filterset_fields = ["patient", "enquiry", "channel", "direction", "status"]

    @action(detail=False, methods=["post"])
    def send(self, request):
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        patient = get_object_or_404(Patient, pk=data["patient"])

        message = send_message(
            patient=patient,
            channel=data["channel"],
            purpose=data["purpose"],
            context=data["context"],
            sent_by=request.user,
            fallback_channel=data["fallback_channel"] or None,
        )
        if message is None:
            return Response({"detail": "Message could not be sent (opted out, no template, or no address)."}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        return Response(MessageSerializer(message).data, status=status.HTTP_201_CREATED)


class ThreadViewSet(WebChatStaffMixin, TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Read-only per-patient-per-channel threads for the Inbox screen —
    unread counts, owner, and SLA timer over the flat `Message` log."""

    serializer_class = ThreadSerializer
    queryset = Thread.objects.all()
    filterset_fields = ["channel", "status", "owner"]

    @action(detail=True, methods=["post"])
    def claim(self, request, pk=None):
        thread = self.get_object()
        thread.owner = request.user
        thread.save(update_fields=["owner"])
        return Response(ThreadSerializer(thread).data)

    @action(detail=True, methods=["post"])
    def mark_read(self, request, pk=None):
        thread = self.get_object()
        scope = {"patient": thread.patient} if thread.patient_id else {"enquiry": thread.enquiry}
        Message.objects.filter(
            channel=thread.channel, direction=Message.Direction.INBOUND, is_read=False, **scope
        ).update(is_read=True)
        return Response(ThreadSerializer(thread).data)


class InboundWebhookView(APIView):
    """Ingress for inbound WhatsApp/SMS/email — resolves the patient by
    phone/email and logs the message into the unified inbox + timeline.
    Like the telephony webhook, production deployments must add the
    concrete provider's signature verification here."""

    permission_classes = [AllowAny]
    serializer_class = MessageSerializer

    def post(self, request, hospital_id, channel):
        payload = request.data
        address = payload.get("from", "")
        body = payload.get("body", "")

        from .delivery import handle_stop_reply

        opted_out = handle_stop_reply(hospital_id, channel, address, body)
        patient = Patient.objects.filter(hospital_id=hospital_id).by_mobile(address).first()
        if patient is None and "@" in address:
            patient = Patient.objects.filter(hospital_id=hospital_id).by_email(address).first()
        if opted_out:
            if patient is not None:
                Message.objects.create(hospital_id=hospital_id, patient=patient, channel=channel, direction=Message.Direction.INBOUND, body=body,
                                       status=Message.Status.RECEIVED, provider_message_id=payload.get("message_id", ""), raw_payload=payload)
            return Response({"detail": "Opted out of marketing messages on this channel."}, status=status.HTTP_202_ACCEPTED)

        if patient is None:
            return Response({"detail": "No matching patient for this address; message dropped."}, status=status.HTTP_202_ACCEPTED)

        message = Message.objects.create(
            hospital_id=hospital_id,
            patient=patient,
            channel=channel,
            direction=Message.Direction.INBOUND,
            body=body,
            status=Message.Status.RECEIVED,
            provider_message_id=payload.get("message_id", ""),
            raw_payload=payload,
            is_read=False,
        )
        touch_thread(patient, channel, inbound=True)
        record_timeline_event(
            patient=patient,
            event_type="message",
            summary=f"{channel} received: {body[:120]}",
            occurred_at=message.created_at,
            source=message,
        )

        # Trigger AI Auto-Reply for inbound WhatsApp/SMS messages
        from apps.licensing.service import feature_enabled

        if not feature_enabled("ai_assist"):
            return Response({"inbound": MessageSerializer(message).data, "ai_auto_reply": None}, status=status.HTTP_201_CREATED)
        from .ai_chatbot import generate_ai_chat_response
        ai_reply = generate_ai_chat_response(
            prompt=body,
            patient_name=patient.full_name or patient.first_name,
            preferred_language=patient.preferred_language,
            hospital=patient.hospital,
        )
        outbound_msg = Message.objects.create(
            hospital_id=hospital_id,
            patient=patient,
            channel=channel,
            direction=Message.Direction.OUTBOUND,
            body=ai_reply,
            status=Message.Status.DELIVERED,
            provider_message_id=f"ai_reply_{message.id}",
        )

        return Response(
            {
                "inbound": MessageSerializer(message).data,
                "ai_auto_reply": MessageSerializer(outbound_msg).data,
            },
            status=status.HTTP_201_CREATED,
        )


class AIChatbotView(APIView):
    """Direct 24x7 Interactive Hospital Assistant endpoint. Requires
    authentication (the project-wide IsAuthenticated default, kept below)
    so TenantMiddleware can resolve `request.user.hospital` — without that,
    a Doctor/Slot query here would run unscoped and leak data across
    hospitals (see apps.core.tenancy). On-premise, also the AI assist
    licence feature."""

    permission_classes = [*api_settings.DEFAULT_PERMISSION_CLASSES, HasFeature("ai_assist")]

    def post(self, request):
        from .ai_chatbot import process_free_text_message, process_interactive_chat_action

        action = str(request.data.get("action", request.data.get("prompt", "main_menu"))).strip()
        language = request.data.get("language", "en")
        payload = request.data.get("payload", {})
        hospital = getattr(request.user, "hospital", None)

        # Free text (the widget's text box, not a button click) is routed
        # through the Ollama intent classifier first — see ai_chatbot.py's
        # module docstring for why that's safe (classify-only, never
        # composes the reply).
        if action == "free_text":
            result = process_free_text_message(
                message=str(payload.get("message", "")),
                preferred_language=language,
                hospital=hospital,
            )
        else:
            result = process_interactive_chat_action(
                action=action,
                payload=payload,
                preferred_language=language,
                hospital=hospital,
            )

        return Response(
            {
                "reply": result["text"],
                "text": result["text"],
                "options": result["options"],
                "step": result.get("step", "main_menu"),
                "confirmed_details": result.get("confirmed_details", None),
                "requires_input": result.get("requires_input", None),
                "pending_slot_id": result.get("pending_slot_id", None),
            },
            status=status.HTTP_200_OK,
        )


class BroadcastCampaignViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """WhatsApp & SMS marketing/outreach campaign broadcast engine."""

    serializer_class = BroadcastCampaignSerializer
    queryset = BroadcastCampaign.objects.all()
    filterset_fields = ["channel", "status", "target_audience"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, created_by=self.request.user)

    @action(detail=False, methods=["get"], url_path="audience-count")
    def audience_count(self, request):
        """People in each target audience right now (before opt-outs)."""
        from .campaigns import audience_counts

        hospital = getattr(request.user, "hospital", None)
        if not hospital:
            return Response({t: 0 for t in BroadcastCampaign.TargetAudience.values})
        return Response(audience_counts(hospital))

    # Not named `dispatch`: that would override APIView.dispatch, the entry
    # point of every request to this viewset.
    @action(detail=True, methods=["post"], url_path="dispatch")
    def dispatch_campaign(self, request, pk=None):
        """Sends the campaign now, or at scheduled_for if that's in the future.
        Messages go out from the background worker; counts fill in as they do."""
        from django.utils import timezone

        from .campaigns import check_ready, start

        campaign = self.get_object()
        if campaign.status not in (BroadcastCampaign.Status.DRAFT, BroadcastCampaign.Status.SCHEDULED, BroadcastCampaign.Status.FAILED):
            return Response({"detail": f"This campaign is already {campaign.get_status_display().lower()}."}, status=status.HTTP_400_BAD_REQUEST)
        problems = check_ready(campaign)
        if problems:
            return Response({"detail": " ".join(problems)}, status=status.HTTP_400_BAD_REQUEST)
        if campaign.scheduled_for and campaign.scheduled_for > timezone.now():
            campaign.status, campaign.error = BroadcastCampaign.Status.SCHEDULED, ""
            campaign.save(update_fields=["status", "error", "updated_at"])
            detail = f"Scheduled for {timezone.localtime(campaign.scheduled_for):%d %b %Y %H:%M}."
        else:
            start(campaign)
            detail = "Sending now. Counts update as messages go out."
        return Response({"detail": detail, "campaign": BroadcastCampaignSerializer(campaign).data})

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        campaign = self.get_object()
        if campaign.status != BroadcastCampaign.Status.SCHEDULED:
            return Response({"detail": "Only a scheduled campaign can be cancelled."}, status=status.HTTP_400_BAD_REQUEST)
        campaign.status = BroadcastCampaign.Status.DRAFT
        campaign.save(update_fields=["status", "updated_at"])
        return Response(BroadcastCampaignSerializer(campaign).data)

    def perform_update(self, serializer):
        from rest_framework.exceptions import ValidationError

        if serializer.instance.status not in (BroadcastCampaign.Status.DRAFT, BroadcastCampaign.Status.FAILED):
            raise ValidationError("A campaign that has been scheduled or sent can't be edited.")
        serializer.save()



