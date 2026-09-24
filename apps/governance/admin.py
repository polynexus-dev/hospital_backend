from django.contrib import admin

from .models import Accreditation, AuditRule, BackupRecord, HelpArticle, ReleaseNote, RetentionPolicy, SecurityEvent, SecurityPolicy

for _m in (Accreditation, AuditRule, BackupRecord, HelpArticle, ReleaseNote, RetentionPolicy, SecurityPolicy):
    admin.site.register(_m)


@admin.register(SecurityEvent)
class SecurityEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event_type", "severity", "username_attempted", "ip_address")
    list_filter = ("event_type", "severity")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
