from django.contrib import admin

from .models import IntakeOutput, MedicationAdministration, NursingNote


@admin.register(NursingNote)
class NursingNoteAdmin(admin.ModelAdmin):
    list_display = ["content_type", "object_id", "nurse", "created_at"]
    list_filter = ["hospital"]


@admin.register(MedicationAdministration)
class MedicationAdministrationAdmin(admin.ModelAdmin):
    list_display = ["medication_name", "dose", "content_type", "object_id", "nurse", "administered_at"]
    list_filter = ["hospital"]


@admin.register(IntakeOutput)
class IntakeOutputAdmin(admin.ModelAdmin):
    list_display = ["content_type", "object_id", "intake_ml", "output_ml", "recorded_at"]
    list_filter = ["hospital"]
