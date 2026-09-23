from django.db.models import Avg, Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.viewsets import TenantScopedViewSetMixin

from .models import Complaint, FeedbackRequest, NPSResponse, ServiceRecoveryTask
from .serializers import (
    ComplaintSerializer,
    FeedbackRequestSerializer,
    NPSResponseSerializer,
    ServiceRecoveryTaskSerializer,
    SubmitNPSSerializer,
)


class FeedbackRequestViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = FeedbackRequestSerializer
    queryset = FeedbackRequest.objects.all()
    filterset_fields = ["status", "doctor", "department"]


class NPSResponseViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Responses only arrive through the public SubmitFeedbackView below —
    this viewset is for staff/analytics read access (§12 voice-of-patient)."""

    serializer_class = NPSResponseSerializer
    queryset = NPSResponse.objects.all()
    filterset_fields = ["category", "doctor", "department"]

    @action(detail=False, methods=["get"], url_path="by-department")
    def by_department(self, request):
        """Per-department NPS rollup: response volume, promoter/detractor
        counts, and average score (§12 voice-of-patient)."""
        rows = self.get_queryset().values("department__name").annotate(
            total=Count("id"),
            promoters=Count("id", filter=Q(category=NPSResponse.Category.PROMOTER)),
            detractors=Count("id", filter=Q(category=NPSResponse.Category.DETRACTOR)),
            avg_score=Avg("score"),
        )
        return Response(list(rows))

    @action(detail=False, methods=["get"], url_path="reputation-summary")
    def reputation_summary(self, request):
        """5-Star Google Review Booster & Overall Reputation Metrics."""
        qs = self.get_queryset()
        total = qs.count()
        promoters = qs.filter(category=NPSResponse.Category.PROMOTER).count()
        passives = qs.filter(category=NPSResponse.Category.PASSIVE).count()
        detractors = qs.filter(category=NPSResponse.Category.DETRACTOR).count()

        # NPS = % Promoters - % Detractors
        nps_score = round(((promoters - detractors) / total) * 100, 1) if total > 0 else 0
        hospital = request.user.hospital
        review_url = getattr(hospital, "google_review_url", "") or "https://g.page/polynexus-hospital/review"

        # Count how many prompts have been sent via WhatsApp
        prompts_sent = 0
        try:
            from apps.communications.models import Message
            prompts_sent = Message.objects.filter(
                hospital=hospital,
                channel="whatsapp",
                body__contains="Google Maps",
            ).count()
        except Exception:
            pass

        return Response({
            "total_responses": total,
            "promoters_count": promoters,
            "passives_count": passives,
            "detractors_count": detractors,
            "nps_score": nps_score,
            "google_review_url": review_url,
            "prompts_sent_count": prompts_sent,
        })

    @action(detail=False, methods=["post"], url_path="update-google-review-url")
    def update_google_review_url(self, request):
        """Updates the hospital's public Google Business / Review profile URL."""
        url = request.data.get("google_review_url", "").strip()
        hospital = request.user.hospital
        if not hospital:
            return Response({"detail": "No hospital found."}, status=400)
        hospital.google_review_url = url
        hospital.save(update_fields=["google_review_url", "updated_at"])
        return Response({"google_review_url": hospital.google_review_url})

    @action(detail=True, methods=["post"], url_path="send-google-review-prompt")
    def send_google_review_prompt(self, request, pk=None):
        """Dispatches an automated WhatsApp 5-Star review prompt to a promoter."""
        nps_response = self.get_object()
        patient = nps_response.patient
        hospital = request.user.hospital
        review_url = getattr(hospital, "google_review_url", "") or "https://g.page/polynexus-hospital/review"
        doctor_str = f" with Dr. {nps_response.doctor.name}" if nps_response.doctor else ""

        message_body = (
            f"Dear {patient.full_name}, thank you for rating your visit{doctor_str} {nps_response.score}/10! "
            f"Would you mind taking 30 seconds to share your review on Google Maps to help others in our community? "
            f"{review_url}"
        )

        from apps.communications.models import Message
        msg = Message.objects.create(
            hospital=hospital,
            patient=patient,
            channel="whatsapp",
            direction="outbound",
            body=message_body,
            status="sent",
        )

        return Response({
            "detail": f"Google Review prompt sent to {patient.full_name} via WhatsApp!",
            "message_id": msg.id,
            "google_review_url": review_url,
        })


class ComplaintViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = ComplaintSerializer
    queryset = Complaint.objects.all()
    filterset_fields = ["status", "department", "owner"]

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        complaint = self.get_object()
        complaint.status = Complaint.Status.CLOSED
        complaint.root_cause = request.data.get("root_cause", complaint.root_cause)
        complaint.closed_at = timezone.now()
        complaint.save(update_fields=["status", "root_cause", "closed_at"])
        return Response(ComplaintSerializer(complaint).data)


class ServiceRecoveryTaskViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = ServiceRecoveryTaskSerializer
    queryset = ServiceRecoveryTask.objects.all()
    filterset_fields = ["status", "owner"]

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        task = self.get_object()
        task.status = ServiceRecoveryTask.Status.RESOLVED
        task.resolution_notes = request.data.get("resolution_notes", task.resolution_notes)
        task.resolved_at = timezone.now()
        task.save(update_fields=["status", "resolution_notes", "resolved_at"])
        return Response(ServiceRecoveryTaskSerializer(task).data)


class SubmitFeedbackView(APIView):
    """Public NPS submission behind the WhatsApp feedback link — no login,
    just the unguessable token (§10). Promoters get the Google review link
    back in the response; detractors are silently routed to the service
    recovery queue (created by apps.feedback.signals)."""

    permission_classes = [AllowAny]

    @extend_schema(request=SubmitNPSSerializer, responses=NPSResponseSerializer)
    def post(self, request, token):
        feedback_request = get_object_or_404(FeedbackRequest, token=token)
        if hasattr(feedback_request, "response"):
            return Response({"detail": "This feedback request has already been answered."}, status=409)

        serializer = SubmitNPSSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        response = NPSResponse.objects.create(
            hospital=feedback_request.hospital,
            feedback_request=feedback_request,
            patient=feedback_request.patient,
            doctor=feedback_request.doctor,
            department=feedback_request.department,
            score=serializer.validated_data["score"],
            comment=serializer.validated_data["comment"],
        )

        payload = NPSResponseSerializer(response).data
        if response.category == NPSResponse.Category.PROMOTER:
            review_url = feedback_request.hospital.google_review_url or "https://g.page/polynexus-hospital/review"
            payload["google_review_url"] = review_url
            try:
                from apps.communications.models import Message
                Message.objects.create(
                    hospital=feedback_request.hospital,
                    patient=feedback_request.patient,
                    channel="whatsapp",
                    direction="outbound",
                    body=f"Thank you {feedback_request.patient.full_name} for rating us {response.score}/10! Please share your review on Google: {review_url}",
                    status="sent",
                )
            except Exception:
                pass

        return Response(payload, status=201)

