from django.contrib import admin

from .models import EDVisit, Triage


@admin.register(EDVisit)
class EDVisitAdmin(admin.ModelAdmin):
    list_display = ["id", "hospital", "patient", "status", "arrived_at"]
    list_filter = ["status", "hospital"]
    search_fields = ["patient__name", "chief_complaint"]


@admin.register(Triage)
class TriageAdmin(admin.ModelAdmin):
    list_display = ["id", "ed_visit", "triage_category", "triaged_at"]
    list_filter = ["triage_category"]
