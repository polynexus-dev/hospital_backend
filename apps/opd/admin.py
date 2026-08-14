from django.contrib import admin

from .models import ClinicalNote, Diagnosis, Encounter, InvestigationOrder, VitalsReading


@admin.register(Encounter)
class EncounterAdmin(admin.ModelAdmin):
    list_display = ["patient", "doctor", "department", "hospital", "created_at"]
    list_filter = ["hospital", "department"]
    search_fields = ["patient__first_name", "patient__last_name", "doctor__name"]


@admin.register(VitalsReading)
class VitalsReadingAdmin(admin.ModelAdmin):
    list_display = ["encounter", "bp_systolic", "bp_diastolic", "pulse", "spo2", "recorded_at"]
    list_filter = ["hospital"]


@admin.register(ClinicalNote)
class ClinicalNoteAdmin(admin.ModelAdmin):
    list_display = ["encounter", "doctor", "finalized_at"]
    list_filter = ["hospital"]


@admin.register(Diagnosis)
class DiagnosisAdmin(admin.ModelAdmin):
    list_display = ["encounter", "description", "diagnosis_type", "finalized_at"]
    list_filter = ["hospital", "diagnosis_type"]
    search_fields = ["description", "icd_code"]


@admin.register(InvestigationOrder)
class InvestigationOrderAdmin(admin.ModelAdmin):
    list_display = ["encounter", "order_type", "status", "created_at"]
    list_filter = ["hospital", "order_type", "status"]
