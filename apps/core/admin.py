from django.contrib import admin

from .models import AuditLog, Department, Hospital


@admin.register(Hospital)
class HospitalAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "state", "is_on_premise", "is_active", "created_at")
    search_fields = ("name", "slug", "city")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "hospital", "code", "is_active")
    list_filter = ("hospital", "is_active")
    search_fields = ("name", "code")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "model_name", "object_id", "method", "path", "status_code")
    list_filter = ("action", "hospital")
    search_fields = ("model_name", "object_id", "path")
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
