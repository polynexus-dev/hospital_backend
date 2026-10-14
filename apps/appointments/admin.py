from django.contrib import admin

from .models import Appointment, Doctor, Slot, SlotTemplate


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ["name", "hospital", "department", "speciality", "is_active"]
    list_filter = ["hospital", "department", "is_active"]
    search_fields = ["name", "speciality"]


@admin.register(SlotTemplate)
class SlotTemplateAdmin(admin.ModelAdmin):
    list_display = ["doctor", "weekday", "start_time", "end_time", "slot_duration_minutes", "is_active"]
    list_filter = ["hospital", "doctor", "weekday"]


@admin.register(Slot)
class SlotAdmin(admin.ModelAdmin):
    list_display = ["doctor", "date", "start_time", "end_time", "is_blocked"]
    list_filter = ["hospital", "doctor", "is_blocked"]


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ["patient", "doctor", "slot", "status", "source"]
    list_filter = ["hospital", "status", "source", "doctor"]
    search_fields = ["patient__uhid"]


from .models import CalendarConnection  # noqa: E402


@admin.register(CalendarConnection)
class CalendarConnectionAdmin(admin.ModelAdmin):
    list_display = ["doctor", "provider", "account_email", "status", "last_synced_at"]
    list_filter = ["hospital", "provider", "status"]
    exclude = ["access_token", "refresh_token"]
