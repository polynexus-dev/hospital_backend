from django.contrib import admin

from .models import DailyMISLog


@admin.register(DailyMISLog)
class DailyMISLogAdmin(admin.ModelAdmin):
    list_display = ["hospital", "report_date", "sent_at", "send_error"]
    list_filter = ["hospital"]
    readonly_fields = ["hospital", "report_date", "summary", "sent_at", "send_error"]
