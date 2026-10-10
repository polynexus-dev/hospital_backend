from rest_framework import serializers

from .models import LicenseRequest, LicenseUsageReport, OnPremiseLicense, SupportTicket, TenantInvoice, TenantSubscription, TenantUsageSnapshot


class TenantSubscriptionSerializer(serializers.ModelSerializer):
    hospital_name = serializers.CharField(source="hospital.name", read_only=True)

    class Meta:
        model = TenantSubscription
        fields = [
            "id", "hospital", "hospital_name", "tier", "billing_cycle", "base_price",
            "max_staff_users", "status", "started_at", "next_billing_date",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class TenantInvoiceSerializer(serializers.ModelSerializer):
    hospital_name = serializers.CharField(source="hospital.name", read_only=True)

    class Meta:
        model = TenantInvoice
        fields = [
            "id", "hospital", "hospital_name", "subscription", "invoice_number",
            "billing_period_start", "billing_period_end", "amount", "status",
            "due_date", "paid_at", "payment_receipt", "notes",
            "created_at", "updated_at",
        ]
        # invoice_number is server-generated only — see
        # apps.saas_admin.services.generate_invoice_number and
        # TenantInvoiceViewSet.perform_create.
        read_only_fields = ["id", "invoice_number", "paid_at", "created_at", "updated_at"]


class TenantUsageSnapshotSerializer(serializers.ModelSerializer):
    hospital_name = serializers.CharField(source="hospital.name", read_only=True)

    class Meta:
        model = TenantUsageSnapshot
        fields = [
            "id", "hospital", "hospital_name", "period_start", "period_end",
            "active_staff_count", "patients_registered_count", "bills_generated_count",
            "storage_bytes_used", "created_at",
        ]
        read_only_fields = fields


class SupportTicketSerializer(serializers.ModelSerializer):
    """Hospital-side: raise/view a ticket about your own hospital.
    Resolution fields are read-only here — only the SaaS-admin surface
    (SaaSSupportTicketSerializer) can change status/assignment/resolution
    notes, matching this codebase's existing "narrower write surface for
    the self-service side" pattern (e.g. UserSerializer's hospital/is_staff
    read-only fields)."""

    raised_by_email = serializers.CharField(source="raised_by.email", read_only=True)

    class Meta:
        model = SupportTicket
        fields = [
            "id", "hospital", "raised_by", "raised_by_email", "subject", "description",
            "category", "priority", "status", "assigned_to", "resolution_notes",
            "resolved_at", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "hospital", "raised_by", "status", "assigned_to",
            "resolution_notes", "resolved_at", "created_at", "updated_at",
        ]


class SaaSSupportTicketSerializer(serializers.ModelSerializer):
    """SaaS-admin side: full cross-tenant visibility and the ability to
    triage (status/assignment/resolution notes) any hospital's ticket."""

    hospital_name = serializers.CharField(source="hospital.name", read_only=True)
    raised_by_email = serializers.CharField(source="raised_by.email", read_only=True)
    assigned_to_email = serializers.CharField(source="assigned_to.email", read_only=True, default=None)

    class Meta:
        model = SupportTicket
        fields = [
            "id", "hospital", "hospital_name", "raised_by", "raised_by_email", "subject",
            "description", "category", "priority", "status", "assigned_to", "assigned_to_email",
            "resolution_notes", "resolved_at", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "hospital", "raised_by", "resolved_at", "created_at", "updated_at"]


class SaaSHospitalSubscriptionSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = TenantSubscription
        fields = ["id", "tier", "billing_cycle", "status", "base_price", "max_staff_users", "next_billing_date"]


class SaaSHospitalSerializer(serializers.ModelSerializer):
    subscription = SaaSHospitalSubscriptionSummarySerializer(read_only=True)
    staff_count = serializers.SerializerMethodField()

    class Meta:
        from apps.core.models import Hospital
        model = Hospital
        fields = [
            "id", "name", "slug", "city", "state", "address",
            "primary_language", "is_active", "enabled_modules", "helpline_phone",
            "subscription", "staff_count", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "subscription", "staff_count"]

    def get_staff_count(self, obj) -> int:
        return obj.users.count()


class LicenseUsageReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = LicenseUsageReport
        fields = ["id", "generated_at", "app_version", "active_users", "beds", "report", "seal_ok", "created_at"]
        read_only_fields = fields


class OnPremiseLicenseSerializer(serializers.ModelSerializer):
    hospital_name = serializers.CharField(source="hospital.name", read_only=True)
    issued_by_email = serializers.CharField(source="issued_by.email", read_only=True, default=None)
    status = serializers.SerializerMethodField()
    days_left = serializers.SerializerMethodField()
    latest_usage = serializers.SerializerMethodField()
    issued_by_code = serializers.CharField(source="issued_by.staff_code", read_only=True, default=None)
    approved_by_code = serializers.CharField(source="approved_by.staff_code", read_only=True, default=None)
    paid = serializers.SerializerMethodField()

    class Meta:
        model = OnPremiseLicense
        fields = [
            "id", "hospital", "hospital_name", "license_id", "tier", "issued_at", "expires_at", "grace_period_days",
            "features", "deployment_id", "enabled_modules", "max_active_users", "max_beds", "machine_fingerprint", "issued_by_email",
            "revoked_at", "revoke_reason", "status", "days_left", "latest_usage", "issued_by_code", "approved_by_code", "paid",
        ]
        read_only_fields = fields

    def get_days_left(self, obj):
        from django.utils import timezone

        return (obj.expires_at - timezone.now()).days

    def get_paid(self, obj):
        from .licence_controls import has_paid_invoice

        return has_paid_invoice(obj)

    def get_latest_usage(self, obj):
        reports = list(obj.usage_reports.all()[:1])  # prefetched, newest first
        return LicenseUsageReportSerializer(reports[0]).data if reports else None

    def get_status(self, obj):
        from django.utils import timezone

        if obj.revoked_at:
            return "revoked"
        return "expired" if obj.expires_at < timezone.now() else "active"


class GenerateLicenseSerializer(serializers.Serializer):
    duration_days = serializers.IntegerField(min_value=1, max_value=3650, default=365)
    grace_period_days = serializers.IntegerField(min_value=0, max_value=90, default=14)
    features = serializers.ListField(child=serializers.CharField(), allow_empty=False)
    deployment_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    hardware_binding = serializers.BooleanField(default=True)
    machine_fingerprint = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    max_users = serializers.IntegerField(min_value=0, default=0)
    max_beds = serializers.IntegerField(min_value=0, default=0)
    tier = serializers.ChoiceField(choices=TenantSubscription.Tier.choices, required=False, allow_blank=True, default="")

    def validate_features(self, value):
        from apps.licensing.features import FEATURE_KEYS

        unknown = sorted(set(value) - set(FEATURE_KEYS))
        if unknown:
            raise serializers.ValidationError(f"Unknown features: {', '.join(unknown)}")
        return [f for f in FEATURE_KEYS if f in value]

    def validate(self, attrs):
        fingerprint = attrs.get("machine_fingerprint", "").strip().lower()
        if attrs["hardware_binding"] and fingerprint != "*" and (len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint)):
            raise serializers.ValidationError({"machine_fingerprint": 'Paste the 64-character fingerprint from get_machine_fingerprint, or "*" for any machine.'})
        attrs["machine_fingerprint"] = fingerprint
        return attrs


class LicenseRequestSerializer(serializers.ModelSerializer):
    hospital_name = serializers.CharField(source="hospital.name", read_only=True)
    requested_by_code = serializers.CharField(source="requested_by.staff_code", read_only=True)
    requested_by_email = serializers.CharField(source="requested_by.email", read_only=True)
    decided_by_code = serializers.CharField(source="decided_by.staff_code", read_only=True, default=None)
    license_id = serializers.CharField(source="license.license_id", read_only=True, default=None)

    class Meta:
        model = LicenseRequest
        fields = ["id", "hospital", "hospital_name", "params", "status", "requested_by_code", "requested_by_email",
                  "decided_by_code", "decided_at", "decision_note", "license", "license_id", "created_at"]
        read_only_fields = fields


class SaaSStaffSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    email = serializers.EmailField()
    name = serializers.CharField(source="get_full_name")
    staff_code = serializers.CharField()
    saas_role = serializers.CharField()
    saas_role_label = serializers.CharField(source="get_saas_role_display")
    can_issue_licenses = serializers.BooleanField()
    is_blocked = serializers.BooleanField()
    is_2fa_enabled = serializers.BooleanField()
