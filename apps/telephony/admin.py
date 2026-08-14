from django.contrib import admin

from .models import Call, CallbackTask, IVRRoute


@admin.register(Call)
class CallAdmin(admin.ModelAdmin):
    list_display = ["from_number", "to_number", "direction", "status", "patient", "enquiry", "department", "operator", "started_at", "duration_seconds"]
    list_filter = ["hospital", "direction", "status", "department"]
    search_fields = ["from_number", "to_number", "provider_call_id"]


@admin.register(CallbackTask)
class CallbackTaskAdmin(admin.ModelAdmin):
    list_display = ["phone_number", "status", "owner", "department", "sla_due_at", "escalation_level"]
    list_filter = ["hospital", "status", "department"]
    search_fields = ["phone_number"]


@admin.register(IVRRoute)
class IVRRouteAdmin(admin.ModelAdmin):
    list_display = ["department", "language", "dial_in_number", "ivr_option_code", "is_active"]
    list_filter = ["hospital", "department", "language", "is_active"]
