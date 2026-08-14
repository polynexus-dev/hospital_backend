from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.viewsets import TenantScopedViewSetMixin

from .models import Document, Patient
from .serializers import (
    DocumentSerializer,
    PatientCRMSerializer,
    PatientLookupSerializer,
    PatientSerializer,
    TimelineEventSerializer,
)


class PatientViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = PatientSerializer
    queryset = Patient.objects.all()
    filterset_fields = ["is_active", "gender", "preferred_language"]
    search_fields = ["first_name", "last_name", "mobile", "alternate_mobile", "email"]

    def get_serializer_class(self):
        # Field-level gating, not record-level — see
        # docs/erp/03-rbac-and-roles.md §2c. A CRM role can still see and
        # edit a Patient row (front-desk registration, corporate/insurance
        # enquiries); it just never sees national_id_number through this
        # endpoint.
        if self.request.user.has_perm("patients.access_clinical_detail"):
            return PatientSerializer
        return PatientCRMSerializer

    @action(detail=False, methods=["get"])
    def lookup(self, request):
        """Auto-identification by phone number — powers telephony screen-pop
        and click-to-call (Part A §1).

        Was `Patient.objects.filter(...)` directly — same class of bug as
        the old TenantScopedViewSetMixin.get_queryset(): that call goes
        through TenantManager, which only scopes by hospital when
        tenancy.get_current_hospital_id() is set, and that contextvar is
        never populated for this project's JWT-authenticated requests (see
        apps.core.viewsets.TenantScopedViewSetMixin's docstring). So this
        action returned matches from every hospital on the platform, not
        just the caller's — verified empirically (a Hospital A front-desk
        user could look up a Hospital B patient's full match by mobile
        number) before switching it to self.get_queryset(), which is
        correctly scoped by self.request.user.hospital_id."""
        mobile = request.query_params.get("mobile", "").strip()
        if not mobile:
            return Response({"detail": "mobile query param is required."}, status=400)
        matches = self.get_queryset().filter(mobile=mobile) | self.get_queryset().filter(alternate_mobile=mobile)
        serializer = PatientLookupSerializer(matches.distinct(), many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def timeline(self, request, pk=None):
        patient = self.get_object()
        events = patient.timeline_events.all()[:200]
        return Response(TimelineEventSerializer(events, many=True).data)


class DocumentViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = DocumentSerializer
    queryset = Document.objects.all()
    filterset_fields = ["patient", "category"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, uploaded_by=self.request.user)


class PrescriptionViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """CRUD viewset for OPD Doctor E-Prescriptions (e-Rx). Every field here
    (diagnosis, medications, lab orders) is clinical content with no
    CRM-safe partial view — gated by RequiresClinicalDetailPermission
    rather than a swapped serializer, see docs/erp/03-rbac-and-roles.md
    §2c and apps.core.permissions.RequiresClinicalDetailPermission."""

    from rest_framework.permissions import IsAuthenticated

    from apps.core.permissions import ActionPermissionRequired, RequiresClinicalDetailPermission, RoleBasedModelPermissions

    from .models import Prescription
    from .serializers import PrescriptionSerializer

    permission_classes = [IsAuthenticated, RoleBasedModelPermissions, ActionPermissionRequired, RequiresClinicalDetailPermission]
    serializer_class = PrescriptionSerializer
    queryset = Prescription.objects.all()
    filterset_fields = ["patient", "doctor"]
    search_fields = ["diagnosis", "notes"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, doctor=self.request.user)

