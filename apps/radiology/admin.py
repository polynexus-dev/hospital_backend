from django.contrib import admin

from .models import RadiologyOrder, RadiologyProcedure, RadiologyReport


@admin.register(RadiologyProcedure)
class RadiologyProcedureAdmin(admin.ModelAdmin):
    list_display = ["name", "modality", "price", "is_active"]
    list_filter = ["hospital", "modality", "is_active"]


@admin.register(RadiologyOrder)
class RadiologyOrderAdmin(admin.ModelAdmin):
    list_display = ["patient", "procedure", "status", "ordered_at"]
    list_filter = ["hospital", "status"]
    search_fields = ["patient__first_name", "patient__last_name"]


@admin.register(RadiologyReport)
class RadiologyReportAdmin(admin.ModelAdmin):
    list_display = ["radiology_order", "reported_by", "finalized_at"]
    list_filter = ["hospital"]
