from django.contrib import admin

from .models import (
    AnaesthesiaRecord,
    ConsumableUsage,
    ImplantUsage,
    OperativeNote,
    OTSchedule,
    PreOpChecklist,
    SurgeryRequest,
)


@admin.register(SurgeryRequest)
class SurgeryRequestAdmin(admin.ModelAdmin):
    list_display = ["id", "hospital", "patient", "status", "created_at"]
    list_filter = ["status", "hospital"]


@admin.register(OTSchedule)
class OTScheduleAdmin(admin.ModelAdmin):
    list_display = ["id", "surgery_request", "operation_theatre_room", "surgeon", "scheduled_start"]


@admin.register(PreOpChecklist)
class PreOpChecklistAdmin(admin.ModelAdmin):
    list_display = ["id", "surgery_request", "consent_obtained", "fasting_confirmed", "site_marked"]


@admin.register(OperativeNote)
class OperativeNoteAdmin(admin.ModelAdmin):
    list_display = ["id", "ot_schedule", "surgeon", "finalized_at"]


@admin.register(AnaesthesiaRecord)
class AnaesthesiaRecordAdmin(admin.ModelAdmin):
    list_display = ["id", "ot_schedule", "anaesthesia_type", "finalized_at"]


@admin.register(ConsumableUsage)
class ConsumableUsageAdmin(admin.ModelAdmin):
    list_display = ["id", "ot_schedule", "item_name", "quantity"]


@admin.register(ImplantUsage)
class ImplantUsageAdmin(admin.ModelAdmin):
    list_display = ["id", "ot_schedule", "implant_name", "serial_number", "quantity"]
