from django.contrib import admin
from django.utils.html import format_html

from .models import AuditLog, Department, Hospital


@admin.register(Hospital)
class HospitalAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "state", "is_on_premise", "is_active", "generate_contract_link", "created_at")
    search_fields = ("name", "slug", "city")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("lead_webhook_token", "generate_contract_link")

    @admin.display(description="Legal Contract")
    def generate_contract_link(self, obj):
        if not obj or not obj.pk:
            return "—"
        url = f"/api/v1/saas-admin/hospitals/{obj.pk}/legal-agreement/?raw=1"
        return format_html(
            '<a href="{}" target="_blank" style="display:inline-block; padding:3px 8px; background:#0f766e; color:#ffffff; border-radius:4px; text-decoration:none; font-weight:600; font-size:11px;">Generate Contract ↗</a>',
            url,
        )


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "hospital", "code", "is_active")
    list_filter = ("hospital", "is_active")
    search_fields = ("name", "code")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor_id", "action", "model_name", "object_id", "method", "path", "status_code")
    list_filter = ("action", "hospital")
    search_fields = ("model_name", "object_id", "path")
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
