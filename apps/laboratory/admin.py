from django.contrib import admin

from .models import LabOrder, LabResult, LabTest, LabTestPackage, SampleCollection


@admin.register(LabTest)
class LabTestAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "department", "price", "is_active"]
    list_filter = ["hospital", "department", "is_active"]
    search_fields = ["name", "code"]


@admin.register(LabTestPackage)
class LabTestPackageAdmin(admin.ModelAdmin):
    list_display = ["name", "price", "is_active"]
    list_filter = ["hospital", "is_active"]


@admin.register(LabOrder)
class LabOrderAdmin(admin.ModelAdmin):
    list_display = ["patient", "status", "ordered_by", "ordered_at"]
    list_filter = ["hospital", "status"]
    search_fields = ["patient__first_name", "patient__last_name"]


@admin.register(SampleCollection)
class SampleCollectionAdmin(admin.ModelAdmin):
    list_display = ["lab_order", "sample_type", "barcode", "collected_at"]
    list_filter = ["hospital"]
    search_fields = ["barcode"]


@admin.register(LabResult)
class LabResultAdmin(admin.ModelAdmin):
    list_display = ["lab_order", "lab_test", "value", "flag", "finalized_at"]
    list_filter = ["hospital", "flag"]
