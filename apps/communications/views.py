from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.viewsets import TenantScopedViewSetMixin
from apps.patients.models import Patient, record_timeline_event

from .models import ConsentOptOut, Message, Template, Thread
from .serializers import (
    ConsentOptOutSerializer,
    MessageSerializer,
    SendMessageSerializer,
    TemplateSerializer,
    ThreadSerializer,
)
from .services import send_message, touch_thread


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
    filterset_fields = ["patient", "channel", "direction", "status"]

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


class ThreadViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
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
        Message.objects.filter(
            patient=thread.patient, channel=thread.channel, direction=Message.Direction.INBOUND, is_read=False
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

        patient = Patient.objects.filter(hospital_id=hospital_id, mobile=address).first()
        if patient is None and "@" in address:
            patient = Patient.objects.filter(hospital_id=hospital_id, email=address).first()

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
    """Direct 24x7 Interactive Hospital Assistant endpoint. Deliberately
    inherits the project-wide IsAuthenticated default (no AllowAny override)
    so TenantMiddleware can resolve `request.user.hospital` — without that,
    a Doctor/Slot query here would run unscoped and leak data across
    hospitals (see apps.core.tenancy)."""

    def post(self, request):
        from .ai_chatbot import process_interactive_chat_action

        action = str(request.data.get("action", request.data.get("prompt", "main_menu"))).strip()
        language = request.data.get("language", "en")
        payload = request.data.get("payload", {})
        hospital = getattr(request.user, "hospital", None)

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


