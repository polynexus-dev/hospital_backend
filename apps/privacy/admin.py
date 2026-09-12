from django.contrib import admin

from .models import DataRightsRequest, GrievanceTicket, Nominee


@admin.register(DataRightsRequest)
class DataRightsRequestAdmin(admin.ModelAdmin):
    list_display = ["patient", "request_type", "status", "hospital", "submitted_at", "sla_due_at"]
    list_filter = ["hospital", "request_type", "status"]


@admin.register(GrievanceTicket)
class GrievanceTicketAdmin(admin.ModelAdmin):
    list_display = ["subject", "patient", "status", "priority", "hospital", "submitted_at", "sla_due_at"]
    list_filter = ["hospital", "status", "priority"]


@admin.register(Nominee)
class NomineeAdmin(admin.ModelAdmin):
    list_display = ["name", "patient", "relationship", "is_active", "hospital"]
    list_filter = ["hospital", "is_active"]

    def get_queryset(self, request):
        return Nominee.objects.all_with_deleted()
