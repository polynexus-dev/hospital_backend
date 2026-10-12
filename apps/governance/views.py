from django.db.models import Q
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.crud import TenantCRUDViewSet
from apps.core.models import AuditLog, Hospital
from apps.core.permissions import CanReviewEmergencyAccess, IsSaaSAdmin
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin

from . import services
from .models import Accreditation, AuditRule, BackupRecord, HelpArticle, ReleaseNote, RetentionPolicy, SecurityEvent, SecurityIncident, SecurityPolicy
from .serializers import (
    AccreditationSerializer,
    AuditRuleSerializer,
    BackupRecordSerializer,
    HelpArticleSerializer,
    ReleaseNoteSerializer,
    RetentionPolicySerializer,
    SecurityEventSerializer,
    SecurityIncidentSerializer,
    SecurityPolicySerializer,
)


class IsHospitalAdmin(CanReviewEmergencyAccess):
    """Same owner/admin/auditor set that reviews break-glass access."""


class SecurityPolicyView(APIView):
    """GET is open to every signed-in user (the frontend needs the idle
    lock settings); PUT/PATCH is for hospital administrators only."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(SecurityPolicySerializer(SecurityPolicy.for_hospital(request.user.hospital_id)).data)

    def patch(self, request):
        if request.user.hospital_id is None or not IsHospitalAdmin().has_permission(request, self):
            return Response({"detail": "Only hospital administrators can change the security policy."}, status=status.HTTP_403_FORBIDDEN)
        policy = SecurityPolicy.for_hospital(request.user.hospital_id)
        serializer = SecurityPolicySerializer(policy, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        before = SecurityPolicySerializer(policy).data
        serializer.save()
        changed = {k: {"old": before[k], "new": v} for k, v in serializer.data.items() if before.get(k) != v}
        services.log_security_event(SecurityEvent.EventType.POLICY_CHANGED, request=request, user=request.user, details=changed)
        return Response(serializer.data)

    put = patch


class SecurityEventViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated, IsHospitalAdmin]
    serializer_class = SecurityEventSerializer
    queryset = SecurityEvent.objects.select_related("user")
    filterset_fields = ["event_type", "severity", "user"]
    search_fields = ["username_attempted", "ip_address"]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.can_cross_tenant:
            return qs
        return qs.filter(hospital_id=self.request.user.hospital_id)

    @action(detail=False, methods=["get"])
    def summary(self, request):
        from datetime import timedelta
        from django.db.models import Count
        from django.utils import timezone

        since = timezone.now() - timedelta(days=int(request.query_params.get("days", 7)))
        qs = self.get_queryset().filter(created_at__gte=since)
        by_type = dict(qs.values_list("event_type").annotate(n=Count("id")))
        by_severity = dict(qs.values_list("severity").annotate(n=Count("id")))
        return Response({"since": since, "by_type": by_type, "by_severity": by_severity})


class AuditRuleViewSet(TenantCRUDViewSet):
    permission_classes = [IsAuthenticated, IsHospitalAdmin]
    serializer_class = AuditRuleSerializer
    queryset = AuditRule.objects.all()
    audited_fields = ("name", "model_name", "actions", "retention_days", "is_active")


class AuditRollbackView(APIView):
    """DOM.3.b manual rollback by a designated IT officer."""

    permission_classes = [IsAuthenticated, IsHospitalAdmin]

    def post(self, request, pk):
        entry = AuditLog.objects.filter(pk=pk).first()
        if entry is None or (not request.user.can_cross_tenant and entry.hospital_id != request.user.hospital_id):
            return Response({"detail": "Audit entry not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            reverted, skipped = services.revert_audited_update(entry, user=request.user, request=request)
        except (ValueError, LookupError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"reverted": reverted, "skipped": skipped})


class RetentionPolicyView(APIView):
    permission_classes = [IsAuthenticated, IsHospitalAdmin]

    def get(self, request):
        return Response(RetentionPolicySerializer(RetentionPolicy.for_hospital(request.user.hospital)).data)

    def patch(self, request):
        policy = RetentionPolicy.for_hospital(request.user.hospital)
        serializer = RetentionPolicySerializer(policy, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    put = patch


class BackupViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated, IsHospitalAdmin]
    serializer_class = BackupRecordSerializer
    queryset = BackupRecord.objects.all()

    def get_queryset(self):
        return BackupRecord.objects.filter(hospital_id=self.request.user.hospital_id)

    def create(self, request):
        if request.user.hospital is None:
            return Response({"detail": "No hospital context."}, status=status.HTTP_400_BAD_REQUEST)
        record = services.run_backup(request.user.hospital, user=request.user)
        code = status.HTTP_201_CREATED if record.status == BackupRecord.Status.SUCCESS else status.HTTP_500_INTERNAL_SERVER_ERROR
        return Response(BackupRecordSerializer(record).data, status=code)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        record = self.get_object()
        try:
            rows = services.restore_backup(record, user=request.user)
        except (ValueError, OSError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"restored_rows": rows})


class HelpArticleViewSet(viewsets.ModelViewSet):
    """Everyone can read (platform-wide + own hospital's articles); hospital
    admins manage their own hospital's articles; platform staff manage the
    platform-wide ones."""

    serializer_class = HelpArticleSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["category", "module"]
    search_fields = ["title", "body", "tags"]

    def get_queryset(self):
        user = self.request.user
        qs = HelpArticle.objects.filter(Q(hospital__isnull=True) | Q(hospital_id=user.hospital_id))
        if self.action in ("list", "retrieve"):
            qs = qs.filter(is_published=True)
        return qs

    def _can_write(self, instance=None):
        user = self.request.user
        if user.can_cross_tenant:
            return True
        if instance is not None and instance.hospital_id is None:
            return False
        return IsHospitalAdmin().has_permission(self.request, self)

    def perform_create(self, serializer):
        if not self._can_write():
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied()
        hospital = None if self.request.user.can_cross_tenant and self.request.data.get("platform_wide") else self.request.user.hospital
        serializer.save(hospital=hospital)

    def perform_update(self, serializer):
        if not self._can_write(serializer.instance):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied()
        serializer.save()

    def perform_destroy(self, instance):
        if not self._can_write(instance):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied()
        instance.delete()


class AccreditationViewSet(TenantCRUDViewSet):
    permission_classes = [IsAuthenticated, IsHospitalAdmin]
    serializer_class = AccreditationSerializer
    queryset = Accreditation.objects.all()
    audited_fields = ("name", "issuing_body", "certificate_number", "issued_on", "valid_until")

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return super().get_permissions()


class PublicAccreditationsView(APIView):
    """Login-page accreditations (AAC.7.b). Public by design — the whole
    point is showing them *before* sign-in."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        slug = (request.GET.get("subdomain") or request.GET.get("hospital") or "").strip().lower()
        hospital = Hospital.objects.filter(slug=slug, is_active=True).first() if slug else None
        if hospital is None:
            return Response([])
        qs = Accreditation.objects.filter(hospital=hospital, show_on_login=True)
        return Response(AccreditationSerializer(qs, many=True, context={"request": request}).data)


class ReleaseNoteViewSet(viewsets.ModelViewSet):
    serializer_class = ReleaseNoteSerializer
    queryset = ReleaseNote.objects.all()
    filterset_fields = ["kind"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsSaaSAdmin()]


class SecurityIncidentViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """Security incidents and personal-data breaches. Anyone signed in to
    the hospital can report one (whoever notices first); hospital
    administrators see the register and move incidents through it."""

    serializer_class = SecurityIncidentSerializer
    queryset = SecurityIncident.objects.select_related("reported_by", "owner")
    filterset_fields = ["status", "severity", "category", "personal_data_affected"]
    http_method_names = ["get", "post", "patch", "head", "options"]  # incidents are closed, never deleted

    def get_permissions(self):
        if self.action == "create":
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsHospitalAdmin()]

    def perform_create(self, serializer):
        from django.core.mail import send_mail

        incident = serializer.save(hospital=self.request.user.hospital, reported_by=self.request.user)
        services.log_security_event(
            SecurityEvent.EventType.INCIDENT_REPORTED, request=self.request, user=self.request.user,
            details={"incident": incident.pk, "category": incident.category, "severity": incident.severity},
        )
        recipients = services.incident_contacts(incident.hospital_id)
        if recipients:
            send_mail(
                f"[Security incident #{incident.pk}] {incident.title}",
                f"Reported by {self.request.user.email}. Severity: {incident.get_severity_display()}.\n\n{incident.description}\n\n"
                f"CERT-In report due by {incident.cert_in_due_at:%d %b %Y %H:%M} if reportable. Open Security & Governance > Incidents.",
                None, recipients, fail_silently=True,
            )

    def _stamp(self, request, field, *, extra=None):
        incident = self.get_object()
        setattr(incident, field, timezone.now())
        for key, value in (extra or {}).items():
            setattr(incident, key, value)
        incident.save()
        self._log("update", incident)
        return Response(SecurityIncidentSerializer(incident).data)

    @action(detail=True, methods=["post"])
    def contain(self, request, pk=None):
        return self._stamp(request, "contained_at", extra={"status": SecurityIncident.Status.CONTAINED, "containment_actions": request.data.get("containment_actions", self.get_object().containment_actions)})

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        return self._stamp(request, "resolved_at", extra={"status": SecurityIncident.Status.RESOLVED})

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        incident = self.get_object()
        missing = [step for step in incident.overdue() + incident.pending_reports() if step]
        if missing:
            return Response({"detail": f"Record these before closing: {', '.join(sorted(set(missing)))}."}, status=status.HTTP_400_BAD_REQUEST)
        if not incident.root_cause:
            return Response({"detail": "Record the root cause before closing."}, status=status.HTTP_400_BAD_REQUEST)
        return self._stamp(request, "closed_at", extra={"status": SecurityIncident.Status.CLOSED})

    @action(detail=True, methods=["post"], url_path="cert-in-reported")
    def cert_in_reported(self, request, pk=None):
        return self._stamp(request, "cert_in_reported_at", extra={"cert_in_reference": str(request.data.get("reference", ""))[:100]})

    @action(detail=True, methods=["post"], url_path="dpb-intimated")
    def dpb_intimated(self, request, pk=None):
        return self._stamp(request, "dpb_intimated_at")

    @action(detail=True, methods=["post"], url_path="dpb-report-submitted")
    def dpb_report_submitted(self, request, pk=None):
        return self._stamp(request, "dpb_report_submitted_at")

    @action(detail=True, methods=["post"], url_path="patients-notified")
    def patients_notified(self, request, pk=None):
        return self._stamp(request, "patients_notified_at")
