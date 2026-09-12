import csv
import io

from django.http import HttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import Hospital
from apps.core.viewsets import TenantScopedViewSetMixin

from .models import Enquiry, TreatmentEstimate
from .serializers import (
    BulkImportRowSerializer,
    EnquiryAssignmentChangeSerializer,
    EnquirySerializer,
    EnquiryStageChangeSerializer,
    LeadWebhookSerializer,
    LoseEnquirySerializer,
    MergeEnquirySerializer,
    MoveStageSerializer,
    ReassignEnquirySerializer,
    TreatmentEstimateSerializer,
)
from .services import merge_enquiries, move_stage, reassign_enquiry
from .treatment_estimate_pdf import render_treatment_estimate_pdf


class EnquiryViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = EnquirySerializer
    queryset = Enquiry.objects.all()
    filterset_fields = ["stage", "source", "department", "assigned_to", "urgency", "patient", "follow_up_date"]
    search_fields = ["name", "mobile", "alternate_mobile", "email", "campaign"]

    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        """Lead 360° audit trail: returns stage change history, ownership
        history, and any treatment estimates."""
        enquiry = self.get_object()
        stage_changes = enquiry.stage_changes.select_related("changed_by").all()
        assignment_changes = enquiry.assignment_changes.select_related("from_owner", "to_owner", "changed_by").all()
        estimates = enquiry.treatment_estimates.all()

        return Response({
            "stage_changes": EnquiryStageChangeSerializer(stage_changes, many=True).data,
            "assignment_changes": EnquiryAssignmentChangeSerializer(assignment_changes, many=True).data,
            "estimates": TreatmentEstimateSerializer(estimates, many=True).data,
        })

    @action(detail=True, methods=["post"], url_path="add-note")
    def add_note(self, request, pk=None):
        """Appends a timestamped coordinator note to the enquiry."""
        enquiry = self.get_object()
        note_text = request.data.get("note", "").strip()
        if not note_text:
            return Response({"detail": "Note content is required."}, status=status.HTTP_400_BAD_REQUEST)

        from django.utils import timezone
        author = request.user.get_full_name() or request.user.email
        stamp = timezone.now().strftime("%d %b %Y %H:%M")
        formatted_entry = f"[{stamp} - {author}]: {note_text}"

        if enquiry.notes:
            enquiry.notes = f"{enquiry.notes}\n{formatted_entry}"
        else:
            enquiry.notes = formatted_entry

        enquiry.save(update_fields=["notes", "updated_at"])
        return Response(EnquirySerializer(enquiry).data)

    @action(detail=False, methods=["get"], url_path="export-csv")
    def export_csv(self, request):
        """Exports filtered enquiries to a CSV file with full marketing, doctor,
        SLA and stage attribution for executive MIS & reporting."""
        queryset = self.filter_queryset(self.get_queryset()).select_related("department", "consulting_doctor", "assigned_to")

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="hospital_crm_leads.csv"'

        # UTF-8 BOM for Microsoft Excel compatibility
        response.write('\ufeff')

        writer = csv.writer(response)
        writer.writerow([
            "Lead ID", "Patient Name", "Mobile", "Alternate Mobile", "Email",
            "Stage", "Urgency", "Score", "Estimated Value (INR)",
            "Department", "Consulting Doctor", "Assigned Owner",
            "Source", "Campaign", "UTM Source", "UTM Medium", "UTM Campaign",
            "Service Requested", "Follow-up Date", "SLA Due At",
            "Lost Reason", "Lost Notes", "Created At", "Internal Notes"
        ])

        for e in queryset:
            writer.writerow([
                e.id,
                e.name,
                e.mobile,
                e.alternate_mobile,
                e.email,
                e.get_stage_display(),
                e.get_urgency_display(),
                e.score,
                e.estimated_value or 0,
                e.department.name if e.department else "",
                getattr(e.consulting_doctor, "name", "") if e.consulting_doctor else "",
                e.assigned_to.get_full_name() if e.assigned_to else (e.assigned_to.email if e.assigned_to else "Unassigned"),
                e.get_source_display(),
                e.campaign,
                e.utm_source,
                e.utm_medium,
                e.utm_campaign,
                e.service_requested,
                e.follow_up_date.isoformat() if e.follow_up_date else "",
                e.sla_due_at.strftime("%Y-%m-%d %H:%M") if e.sla_due_at else "",
                e.get_lost_reason_display() if e.lost_reason else "",
                e.lost_notes,
                e.created_at.strftime("%Y-%m-%d %H:%M"),
                e.notes,
            ])

        return response

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

    @action(detail=False, methods=["get"], url_path="webhook-config")
    def webhook_config(self, request):
        hospital = request.user.hospital
        if not hospital:
            return Response({"detail": "No hospital tenant configured."}, status=status.HTTP_400_BAD_REQUEST)
        token = str(hospital.lead_webhook_token)
        webhook_url = request.build_absolute_uri(f"/api/v1/enquiries/lead-webhook/{token}/")
        return Response({
            "token": token,
            "webhook_url": webhook_url,
            "supported_sources": [c[0] for c in Enquiry.Source.choices],
            "sample_payload": {
                "name": "Suresh Patel",
                "mobile": "+919876543210",
                "email": "suresh.patel@example.com",
                "source": "meta",
                "campaign": "Orthopedics Joint Replacement Campaign 2026",
                "service_requested": "Total Knee Replacement",
                "utm_source": "facebook",
                "utm_medium": "cpc",
                "utm_campaign": "joint_pain_pune",
            },
        })


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


class TreatmentEstimateViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """IPD & Surgical counseling conversion pipeline & estimate generator."""

    serializer_class = TreatmentEstimateSerializer
    queryset = TreatmentEstimate.objects.all().select_related("patient", "enquiry", "doctor", "department", "hospital")
    filterset_fields = ["stage", "insurance_preauth_status", "payment_mode", "doctor", "department", "patient"]
    search_fields = ["procedure_name", "diagnosis", "patient__first_name", "patient__last_name", "enquiry__name", "notes"]

    @action(detail=True, methods=["get"], url_path="pdf")
    def download_pdf(self, request, pk=None):
        estimate = self.get_object()
        pdf_bytes = render_treatment_estimate_pdf(estimate)
        sanitized_name = "".join(c for c in estimate.procedure_name if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
        filename = f"Estimate_EST-{estimate.pk:05d}_{sanitized_name or 'Surgical_Estimate'}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        return response

    @action(detail=True, methods=["post"], url_path="convert-admission")
    def convert_admission(self, request, pk=None):
        """Marks estimate as converted to admission / OT schedule."""
        estimate = self.get_object()
        estimate.stage = TreatmentEstimate.Stage.CONVERTED
        estimate.save(update_fields=["stage"])
        return Response(TreatmentEstimateSerializer(estimate).data)

