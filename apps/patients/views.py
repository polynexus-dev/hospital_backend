from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

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
    filterset_fields = ["is_active", "gender", "preferred_language"]
    # mobile/alternate_mobile are encrypted at rest (Part A #2) and
    # deliberately excluded here — SearchFilter's icontains lookup against
    # an encrypted column silently matches nothing rather than erroring.
    # Exact phone lookup still works, via the `lookup` action below.
    search_fields = ["first_name", "last_name", "email"]

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
        correctly scoped by self.request.user.hospital_id.

        mobile/alternate_mobile are now encrypted (Part A #2), so this can
        no longer filter the columns directly — by_mobile() goes through
        the blind-index columns instead (see PatientQuerySet.by_mobile)."""
        mobile = request.query_params.get("mobile", "").strip()
        if not mobile:
            return Response({"detail": "mobile query param is required."}, status=400)
        matches = self.get_queryset().by_mobile(mobile)
        serializer = PatientLookupSerializer(matches.distinct(), many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def timeline(self, request, pk=None):
        patient = self.get_object()
        events = patient.timeline_events.all()[:200]
        return Response(TimelineEventSerializer(events, many=True).data)


class DocumentViewSet(SoftDeleteViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = DocumentSerializer
    queryset = Document.objects.all()
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
    filterset_fields = ["patient", "doctor"]
    # diagnosis/notes are encrypted at rest (Part A #2) — see the same
    # caveat on PatientViewSet.search_fields above.

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, doctor=self.request.user)

