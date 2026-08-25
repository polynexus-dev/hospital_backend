from django.contrib import admin

from .models import SupportTicket, TenantInvoice, TenantSubscription, TenantUsageSnapshot


@admin.register(TenantSubscription)
class TenantSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("hospital", "tier", "billing_cycle", "status", "max_staff_users", "next_billing_date")
    list_filter = ("tier", "billing_cycle", "status")
    search_fields = ("hospital__name",)


@admin.register(TenantInvoice)
class TenantInvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "hospital", "amount", "status", "due_date", "paid_at")
    list_filter = ("status",)
    search_fields = ("invoice_number", "hospital__name")


@admin.register(TenantUsageSnapshot)
class TenantUsageSnapshotAdmin(admin.ModelAdmin):
    list_display = ("hospital", "period_start", "active_staff_count", "patients_registered_count", "bills_generated_count", "storage_bytes_used")
    list_filter = ("period_start",)
    search_fields = ("hospital__name",)


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("subject", "hospital", "category", "priority", "status", "assigned_to", "created_at")
    list_filter = ("status", "category", "priority")
    search_fields = ("subject", "hospital__name")
