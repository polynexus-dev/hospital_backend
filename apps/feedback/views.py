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

