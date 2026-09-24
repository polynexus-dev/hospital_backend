from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.crud import TenantCRUDViewSet, TenantModelSerializer, model_serializer
from apps.core.permissions import RequiresViewPermission

from . import services
from .models import GovtScheme, SchemeBeneficiary, SchemeCase, SchemeCasePackage, SchemePackage


def _err(e):
    return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class GovtSchemeViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(GovtScheme)
    queryset = GovtScheme.objects.all()
    filterset_fields = ["billing_mode", "is_active"]
    audited_fields = ("billing_mode", "empanelment_number", "copay_percent", "is_active")
    action_permissions = {"install_defaults": "schemes.add_govtscheme", "import_packages": "schemes.add_schemepackage"}

    @action(detail=False, methods=["post"], url_path="install-defaults")
    def install_defaults(self, request):
        """Adds PM-JAY, CGHS and ECHS if missing (edit empanelment number etc. after)."""
        return Response({"added": services.install_default_schemes(request.user.hospital_id)})

    @action(detail=True, methods=["post"], url_path="import-packages")
    def import_packages(self, request, pk=None):
        """Upload the empanelled package list: CSV `file` (or `csv` text) with code, name, rate
        [, specialty, expected_los_days, preauth_required, implant_included, includes]."""
        scheme = self.get_object()
        upload = request.FILES.get("file")
        text = upload.read().decode("utf-8-sig", errors="replace") if upload else request.data.get("csv", "")
        if not text:
            return _err("Send a CSV file as `file`, or CSV text as `csv`.")
        try:
            return Response(services.import_packages(scheme, text))
        except services.SchemeError as e:
            return _err(e)


class SchemePackageViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(SchemePackage, extra={"scheme_code": serializers.CharField(source="scheme.code", read_only=True)})
    queryset = SchemePackage.objects.select_related("scheme")
    filterset_fields = ["scheme", "specialty", "is_active", "preauth_required"]
    search_fields = ["code", "name", "specialty"]
    audited_fields = ("rate", "is_active")


class SchemeBeneficiaryViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(SchemeBeneficiary, read_only=("verified_at", "verified_by"), extra={
        "patient_name": serializers.CharField(source="patient.full_name", read_only=True),
        "scheme_code": serializers.CharField(source="scheme.code", read_only=True),
    })
    queryset = SchemeBeneficiary.objects.select_related("patient", "scheme")
    filterset_fields = ["patient", "scheme", "eligibility"]
    search_fields = ["beneficiary_id", "family_id", "patient__first_name", "patient__last_name"]
    audited_fields = ("beneficiary_id", "eligibility")

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        """POST {eligibility: eligible|ineligible, card_valid_to?, notes?} after checking the scheme portal / BIS."""
        b = self.get_object()
        result = request.data.get("eligibility")
        if result not in ("eligible", "ineligible"):
            return _err("eligibility must be eligible or ineligible.")
        b.eligibility, b.verified_at, b.verified_by = result, timezone.now(), request.user
        if request.data.get("card_valid_to"):
            b.card_valid_to = request.data["card_valid_to"]
        if request.data.get("notes"):
            b.notes = request.data["notes"]
        b.save()
        self._log("update", b)
        return Response(self.get_serializer(b).data)


class CasePackageSerializer(serializers.ModelSerializer):
    code = serializers.CharField(source="package.code", read_only=True)
    name = serializers.CharField(source="package.name", read_only=True)

    class Meta:
        model = SchemeCasePackage
        fields = ["id", "package", "code", "name", "quantity", "rate"]


class SchemeCaseSerializer(TenantModelSerializer):
    patient_name = serializers.CharField(source="beneficiary.patient.full_name", read_only=True)
    scheme_code = serializers.CharField(source="beneficiary.scheme.code", read_only=True)
    beneficiary_label = serializers.CharField(source="beneficiary.__str__", read_only=True)
    packages = CasePackageSerializer(source="case_packages", many=True, read_only=True)
    document_checklist = serializers.ListField(source="beneficiary.scheme.document_checklist", read_only=True)

    class Meta:
        model = SchemeCase
        fields = "__all__"
        read_only_fields = [
            "status", "preauth_submitted_at", "preauth_amount_requested", "preauth_amount_approved", "preauth_decided_at", "claim_amount",
            "claim_due_by", "claim_submitted_at", "approved_amount", "settled_amount", "settled_on", "utr_number", "history", "created_by",
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        ben = attrs.get("beneficiary") or getattr(self.instance, "beneficiary", None)
        adm = attrs.get("admission") or getattr(self.instance, "admission", None)
        if ben and adm and adm.patient_id != ben.patient_id:
            raise serializers.ValidationError({"admission": "This admission belongs to a different patient."})
        if ben and ben.eligibility == SchemeBeneficiary.Eligibility.INELIGIBLE:
            raise serializers.ValidationError({"beneficiary": "This beneficiary was verified as not eligible."})
        return attrs


class SchemeCaseViewSet(TenantCRUDViewSet):
    """Pre-auth → treatment → claim → settlement for one scheme patient."""

    permission_classes = TenantCRUDViewSet.permission_classes + [RequiresViewPermission]
    serializer_class = SchemeCaseSerializer
    queryset = SchemeCase.objects.select_related("beneficiary__patient", "beneficiary__scheme", "admission").prefetch_related("case_packages__package")
    filterset_fields = ["status", "beneficiary", "beneficiary__scheme", "admission"]
    audited_fields = ("status", "claim_amount", "approved_amount", "settled_amount")
    actor_field = "created_by"
    action_permissions = {name: "schemes.change_schemecase" for name in ("add_package", "remove_package", "apply_to_bill", "transition", "documents")}
    action_permissions.update({"claim_pack": "schemes.view_schemecase", "dashboard": "schemes.view_schemecase"})

    @action(detail=True, methods=["post"], url_path="add-package")
    def add_package(self, request, pk=None):
        """POST {package, quantity?} — the package rate is frozen on the case."""
        case = self.get_object()
        pkg = SchemePackage.objects.filter(pk=request.data.get("package"), scheme=case.scheme, is_active=True).first()
        if pkg is None:
            return _err("Package not found for this scheme.")
        if case.status not in ("draft", "preauth_query", "preauth_rejected"):
            return _err("Packages can only change before pre-auth is approved.")
        SchemeCasePackage.objects.update_or_create(case=case, package=pkg, defaults={"quantity": int(request.data.get("quantity") or 1), "rate": pkg.rate})
        return Response(self.get_serializer(case).data)

    @action(detail=True, methods=["post"], url_path="remove-package")
    def remove_package(self, request, pk=None):
        case = self.get_object()
        if case.status not in ("draft", "preauth_query", "preauth_rejected"):
            return _err("Packages can only change before pre-auth is approved.")
        case.case_packages.filter(package_id=request.data.get("package")).delete()
        return Response(self.get_serializer(case).data)

    @action(detail=True, methods=["post"], url_path="apply-to-bill")
    def apply_to_bill(self, request, pk=None):
        """Re-prices the running bill for the scheme and computes the claim amount."""
        case = self.get_object()
        try:
            bill = services.apply_to_bill(case)
        except services.SchemeError as e:
            return _err(e)
        return Response({"bill": bill.pk, "bill_number": bill.bill_number, "net_amount": bill.net_amount, "claim_amount": case.claim_amount,
                         "patient_payable": max(bill.net_amount - case.claim_amount, 0)})

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def transition(self, request, pk=None):
        """POST {to, note?, preauth_number?, amount?, claim_number?, deduction_reason?, utr_number?, override_documents?}"""
        case = self.get_object()
        fields = {k: request.data.get(k) for k in ("preauth_number", "amount", "claim_number", "deduction_reason", "utr_number", "override_documents")}
        try:
            services.transition(case, request.data.get("to", ""), request.user, note=request.data.get("note", ""), **fields)
        except services.SchemeError as e:
            transaction.set_rollback(True)
            return _err(e)
        self._log("update", case)
        return Response(self.get_serializer(case).data)

    @action(detail=True, methods=["post"])
    def documents(self, request, pk=None):
        """POST {document: <checklist item>, attached: true|false}"""
        case = self.get_object()
        doc = request.data.get("document")
        if doc not in case.scheme.document_checklist:
            return _err("Not an item on this scheme's checklist.")
        case.documents = {**case.documents, doc: bool(request.data.get("attached", True))}
        case.save(update_fields=["documents"])
        return Response(self.get_serializer(case).data)

    @action(detail=True, methods=["get"], url_path="claim-pack")
    def claim_pack(self, request, pk=None):
        from .claim_pack import render_claim_pack

        case = self.get_object()
        resp = HttpResponse(render_claim_pack(case), content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="claim-{case.scheme.code}-{case.pk}.pdf"'
        return resp

    @action(detail=False, methods=["get"])
    def dashboard(self, request):
        """Claims by status, overdue submissions, outstanding amount, settlement time, rejection rate."""
        return Response(services.dashboard(request.user.hospital_id))
