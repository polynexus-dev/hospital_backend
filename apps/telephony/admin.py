from django.contrib import admin

from .models import Call, CallbackTask, IVRMenu, IVRNode, IVRRoute, IVRSession


@admin.register(Call)
class CallAdmin(admin.ModelAdmin):
    list_display = ["from_number", "to_number", "direction", "status", "patient", "enquiry", "department", "operator", "started_at", "duration_seconds", "ai_status", "missed_opportunity"]
    list_filter = ["hospital", "direction", "status", "department", "ai_status", "ai_lead_class", "missed_opportunity"]
    search_fields = ["from_number", "to_number", "provider_call_id"]


@admin.register(CallbackTask)
class CallbackTaskAdmin(admin.ModelAdmin):
    list_display = ["phone_number", "status", "owner", "department", "sla_due_at", "escalation_level"]
    list_filter = ["hospital", "status", "source", "department"]
    search_fields = ["phone_number"]


@admin.register(IVRRoute)
class IVRRouteAdmin(admin.ModelAdmin):
    list_display = ["department", "language", "dial_in_number", "ivr_option_code", "is_active"]
    list_filter = ["hospital", "department", "language", "is_active"]


@admin.register(IVRMenu)
class IVRMenuAdmin(admin.ModelAdmin):
    list_display = ["name", "hospital", "dial_in_number", "is_active"]
    list_filter = ["hospital", "is_active"]


@admin.register(IVRNode)
class IVRNodeAdmin(admin.ModelAdmin):
    list_display = ["label", "menu", "parent", "digit", "action", "department"]
    list_filter = ["hospital", "action"]


@admin.register(IVRSession)
class IVRSessionAdmin(admin.ModelAdmin):
    list_display = ["caller", "menu", "state", "department", "queued_at", "answered_at", "ended_at", "is_test"]
    list_filter = ["hospital", "state", "is_test"]
    search_fields = ["caller", "provider_call_id"]
