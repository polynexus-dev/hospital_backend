from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import (
    ActionPermissionRequired,
    RequiresClinicalDetailPermission,
    RequiresViewPermission,
    RoleBasedModelPermissions,
)
from apps.core.viewsets import SoftDeleteViewSetMixin, TenantScopedViewSetMixin

from .models import Document, Patient
from .serializers import (
    DocumentSerializer,
    PatientLookupSerializer,
    PatientSerializer,
    TimelineEventSerializer,
)


class PatientViewSet(SoftDeleteViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = PatientSerializer
    queryset = Patient.objects.all()
    # list/retrieve stay open to every role whose template grants
    # "patients" at all (see apps.core.tests.
    # test_restricted_role_can_still_list_and_retrieve_patients — front
    # desk/telephony/billing/clinical roles all legitimately need this).
    # RequiresViewPermission only closes the gap for the few templates that
    # deliberately omit "patients" entirely (hr_manager, purchase_manager,
    # inventory_manager — see apps.accounts.permission_templates), who were
    # never meant to see a patient record at all. lookup/timeline are
    # custom actions RoleBasedModelPermissions never gates (see
    # apps.core.tests.test_custom_actions_are_not_gated_by_the_model_permission_check)
    # so they need the same check spelled out explicitly via
    # ActionPermissionRequired — lookup in particular is a phone-number
    # search that would otherwise let those same excluded roles reach any
    # patient one at a time instead of via `list`.
    permission_classes = [IsAuthenticated, RoleBasedModelPermissions, RequiresViewPermission, ActionPermissionRequired]
    action_permissions = {"lookup": "patients.view_patient", "timeline": "patients.view_patient", "recalls": "patients.view_patient"}
    filterset_fields = ["is_active", "gender", "preferred_language"]
    # mobile/alternate_mobile are encrypted at rest (Part A #2) and
    # deliberately excluded here — SearchFilter's icontains lookup against
    # an encrypted column silently matches nothing rather than erroring.
    # Exact phone lookup still works, via the `lookup` action below.
    search_fields = ["first_name", "last_name", "email"]

    @action(detail=False, methods=["get"])
    def lookup(self, request):
        """Auto-identification by phone number — powers telephony screen-pop
        and click-to-call (Part A §1)."""
        mobile = request.query_params.get("mobile", "").strip()
        if not mobile:
            return Response({"detail": "mobile query param is required."}, status=400)
        matches = self.get_queryset().by_mobile(mobile)
        serializer = PatientLookupSerializer(matches.distinct(), many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], url_path="recalls")
    def recalls(self, request):
        """Clinical recall & preventive care retention hub (§CRM Growth)."""
        from django.utils import timezone
        from datetime import timedelta

        now = timezone.now()
        today = now.date()
        week_ahead = today + timedelta(days=7)

        qs = self.get_queryset().filter(next_recall_due_at__isnull=False)

        # Compute summary counts across entire active patient pool for this hospital
        overdue_count = qs.filter(next_recall_due_at__lt=now).count()
        today_count = qs.filter(next_recall_due_at__date=today).count()
        week_count = qs.filter(next_recall_due_at__date__gte=today, next_recall_due_at__date__lte=week_ahead).count()

        # Apply user filters
        filter_status = request.query_params.get("status")
        if filter_status == "overdue":
            qs = qs.filter(next_recall_due_at__lt=now)
        elif filter_status == "due_today":
            qs = qs.filter(next_recall_due_at__date=today)
        elif filter_status == "due_this_week":
            qs = qs.filter(next_recall_due_at__date__gte=today, next_recall_due_at__date__lte=week_ahead)
        elif filter_status == "upcoming":
            qs = qs.filter(next_recall_due_at__gt=now)

        reason = request.query_params.get("reason")
        if reason:
            qs = qs.filter(recall_reason__icontains=reason)

        qs = qs.order_by("next_recall_due_at")[:100]

        items = []
        for p in qs:
            due_at = p.next_recall_due_at
            if due_at < now:
                urgency = "overdue"
            elif due_at.date() == today:
                urgency = "due_today"
            else:
                urgency = "upcoming"

            items.append({
                "id": p.id,
                "full_name": p.full_name,
                "uhid": getattr(p, "uhid", ""),
                "mobile": getattr(p, "mobile", ""),
                "preferred_language": p.preferred_language,
                "next_recall_due_at": p.next_recall_due_at.isoformat(),
                "recall_reason": p.recall_reason or "Preventive Clinical Follow-Up",
                "urgency": urgency,
            })

        return Response({
            "summary": {
                "overdue": overdue_count,
                "due_today": today_count,
                "due_this_week": week_count,
                "total_recalls": overdue_count + today_count + week_count,
            },
            "results": items,
        })

    @action(detail=True, methods=["get"])
    def timeline(self, request, pk=None):
        patient = self.get_object()
        events = patient.timeline_events.all()[:200]
        return Response(TimelineEventSerializer(events, many=True).data)



class DocumentViewSet(SoftDeleteViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = DocumentSerializer
    queryset = Document.objects.all()
    # Same reasoning as PatientViewSet.permission_classes above — Document
    # is in the same "patients" app, so the same templates that omit it
    # (hr_manager, purchase_manager, inventory_manager) should not be able
    # to read uploaded patient documents either.
    permission_classes = [IsAuthenticated, RoleBasedModelPermissions, RequiresViewPermission]
    filterset_fields = ["patient", "category"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, uploaded_by=self.request.user)


class PrescriptionViewSet(SoftDeleteViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """CRUD viewset for OPD Doctor E-Prescriptions (e-Rx)."""

    from .models import Prescription
    from .serializers import PrescriptionSerializer

    serializer_class = PrescriptionSerializer
    queryset = Prescription.objects.all()
    # "patients" is a coarse, per-app Django permission (see
    # apps.accounts.permission_templates's module docstring) — many
    # non-clinical roles (front_desk, billing_executive, finance_manager)
    # legitimately hold it for Patient demographics/billing, which would
    # otherwise also implicitly cover this viewset's diagnosis/medications/
    # lab_orders content. RequiresClinicalDetailPermission is the same
    # extra gate every other clinical app (opd/ipd/pharmacy/laboratory/...)
    # already applies for exactly this reason.
    permission_classes = [IsAuthenticated, RoleBasedModelPermissions, RequiresClinicalDetailPermission]
    filterset_fields = ["patient", "doctor"]
    # diagnosis/notes are encrypted at rest (Part A #2) — see the same
    # caveat on PatientViewSet.search_fields above.

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, doctor=self.request.user)

    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, pk=None):
        prescription = self.get_object()
        from django.http import HttpResponse
        from .prescription_pdf import render_prescription_pdf

        pdf_bytes = render_prescription_pdf(prescription)
        filename = f"Prescription_{getattr(prescription.patient, 'uhid', 'Rx')}_{prescription.id}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


