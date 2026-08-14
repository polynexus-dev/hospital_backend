from django.contrib import admin

from .models import Admission, BedAllocation, DischargeSummary, DoctorProgressNote, WardTransfer


@admin.register(Admission)
class AdmissionAdmin(admin.ModelAdmin):
    list_display = ["patient", "admitting_doctor", "bed", "status", "admission_type", "admitted_at", "discharged_at"]
    list_filter = ["hospital", "status", "admission_type"]
    search_fields = ["patient__first_name", "patient__last_name"]


@admin.register(BedAllocation)
class BedAllocationAdmin(admin.ModelAdmin):
    list_display = ["admission", "bed", "allocated_at", "released_at"]
    list_filter = ["hospital"]


@admin.register(WardTransfer)
class WardTransferAdmin(admin.ModelAdmin):
    list_display = ["admission", "from_bed", "to_bed", "requested_at", "approved_by", "transferred_at"]
    list_filter = ["hospital"]


@admin.register(DoctorProgressNote)
class DoctorProgressNoteAdmin(admin.ModelAdmin):
    list_display = ["admission", "doctor", "finalized_at", "created_at"]
    list_filter = ["hospital"]


@admin.register(DischargeSummary)
class DischargeSummaryAdmin(admin.ModelAdmin):
    list_display = ["admission", "discharge_type", "finalized_at", "created_at"]
    list_filter = ["hospital", "discharge_type"]
