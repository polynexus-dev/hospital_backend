from django.contrib import admin

from .models import ICUAdmission, ICUDailyProgressNote, VentilatorLog


@admin.register(ICUAdmission)
class ICUAdmissionAdmin(admin.ModelAdmin):
    list_display = ["id", "hospital", "admission", "bed", "ventilator_required", "admitted_at"]
    list_filter = ["ventilator_required", "hospital"]


@admin.register(VentilatorLog)
class VentilatorLogAdmin(admin.ModelAdmin):
    list_display = ["id", "icu_admission", "mode", "recorded_by", "recorded_at"]


@admin.register(ICUDailyProgressNote)
class ICUDailyProgressNoteAdmin(admin.ModelAdmin):
    list_display = ["id", "icu_admission", "doctor", "finalized_at"]
