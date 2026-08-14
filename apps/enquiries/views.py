import csv
import io

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import Hospital
from apps.core.viewsets import TenantScopedViewSetMixin

from .models import Enquiry
from .serializers import (
    BulkImportRowSerializer,
    EnquirySerializer,
    LeadWebhookSerializer,
    LoseEnquirySerializer,
    MergeEnquirySerializer,
    MoveStageSerializer,
    ReassignEnquirySerializer,
)
from .services import merge_enquiries, move_stage, reassign_enquiry


class EnquiryViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = EnquirySerializer
    queryset = Enquiry.objects.all()
    filterset_fields = ["stage", "source", "department", "assigned_to", "urgency", "patient"]
    search_fields = ["name", "mobile", "alternate_mobile", "email", "campaign"]

    @action(detail=True, methods=["post"], url_path="move-stage")
    def move_stage_action(self, request, pk=None):
        enquiry = self.get_object()
        serializer = MoveStageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        move_stage(enquiry, serializer.validated_data["stage"], changed_by=request.user)
        return Response(EnquirySerializer(enquiry).data)

    @action(detail=True, methods=["post"])
    def lose(self, request, pk=None):
        enquiry = self.get_object()
        serializer = LoseEnquirySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        enquiry.lost_reason = serializer.validated_data["lost_reason"]
        enquiry.lost_notes = serializer.validated_data["lost_notes"]
        enquiry.save(update_fields=["lost_reason", "lost_notes"])
        move_stage(enquiry, Enquiry.Stage.LOST, changed_by=request.user)
        return Response(EnquirySerializer(enquiry).data)

    @action(detail=True, methods=["post"])
    def reassign(self, request, pk=None):
        """Manual ownership change, distinct from the auto-assignment
        `assign_enquiry` runs on creation — routed through the same
        `reassign_enquiry` service so both paths log to
        EnquiryAssignmentChange (§2)."""
        enquiry = self.get_object()
        serializer = ReassignEnquirySerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        reassign_enquiry(
            enquiry,
            serializer.validated_data["owner"],
            changed_by=request.user,
            reason=serializer.validated_data["reason"] or "manual reassignment",
        )
        return Response(EnquirySerializer(enquiry).data)

    @action(detail=True, methods=["post"])
    def merge(self, request, pk=None):
        """Merges this enquiry (the duplicate) into another one (the
        primary) — consolidates notes and closes the duplicate out of the
        pipeline (§2)."""
        duplicate = self.get_object()
        serializer = MergeEnquirySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        primary = serializer.validated_data["primary_id"]
        if primary.hospital_id != duplicate.hospital_id:
            raise ValidationError("Cannot merge enquiries across hospitals.")
        merge_enquiries(primary, duplicate, merged_by=request.user)
        return Response(EnquirySerializer(primary).data)

    @action(detail=False, methods=["post"], url_path="bulk-import", parser_classes=[MultiPartParser, FormParser])
    def bulk_import(self, request):
        """CSV bulk import / historical enquiry migration (§2). Expects a
        `file` field with columns: name, mobile, email, source,
        service_requested."""
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"detail": "file is required."}, status=status.HTTP_400_BAD_REQUEST)

        hospital = request.user.hospital
        reader = csv.DictReader(io.StringIO(upload.read().decode("utf-8-sig")))

        created, errors = 0, []
        for line_number, row in enumerate(reader, start=2):
            row_serializer = BulkImportRowSerializer(data=row)
            if not row_serializer.is_valid():
                errors.append({"line": line_number, "errors": row_serializer.errors})
                continue
            Enquiry.objects.create(hospital=hospital, **row_serializer.validated_data)
            created += 1

        return Response({"created": created, "errors": errors}, status=status.HTTP_201_CREATED if created else status.HTTP_400_BAD_REQUEST)


class LeadWebhookView(APIView):
    """Public inbound lead-capture endpoint for website contact forms and
    Meta/Google lead-ad integrations (§2). Auth is the per-hospital secret
    in the URL itself (Hospital.lead_webhook_token) rather than a session
    or JWT — the caller is a third-party form builder, not a logged-in
    user. Enquiry.post_save (apps.enquiries.signals) already handles
    duplicate detection and auto-assignment on create, so this view only
    has to validate the payload and create the row."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, token):
        hospital = Hospital.objects.filter(lead_webhook_token=token, is_active=True).first()
        if hospital is None:
            return Response({"detail": "Invalid webhook token."}, status=status.HTTP_404_NOT_FOUND)

        serializer = LeadWebhookSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        enquiry = Enquiry.objects.create(hospital=hospital, **serializer.validated_data)
        return Response({"id": enquiry.id}, status=status.HTTP_201_CREATED)
