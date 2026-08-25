from rest_framework import serializers

from .models import SupportTicket, TenantInvoice, TenantSubscription, TenantUsageSnapshot


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
        read_only_fields = ["id", "paid_at", "created_at", "updated_at"]


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
