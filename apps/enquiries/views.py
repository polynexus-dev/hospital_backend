import csv
import io

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from apps.core.viewsets import TenantScopedViewSetMixin

from .models import Enquiry
from .serializers import (
    BulkImportRowSerializer,
    EnquirySerializer,
    LoseEnquirySerializer,
    MoveStageSerializer,
)
from .services import move_stage


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
        enquiry.save(update_fields=["lost_reason"])
        move_stage(enquiry, Enquiry.Stage.LOST, changed_by=request.user)
        return Response(EnquirySerializer(enquiry).data)

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
