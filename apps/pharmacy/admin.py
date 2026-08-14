from django.contrib import admin

from .models import DispenseRecord, Medicine, MedicineBatch, StockAdjustment, Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ["name", "contact_person", "phone", "is_active"]
    list_filter = ["hospital", "is_active"]


@admin.register(Medicine)
class MedicineAdmin(admin.ModelAdmin):
    list_display = ["name", "generic_name", "form", "reorder_level", "is_active"]
    list_filter = ["hospital", "form", "is_active"]
    search_fields = ["name", "generic_name"]


@admin.register(MedicineBatch)
class MedicineBatchAdmin(admin.ModelAdmin):
    list_display = ["medicine", "batch_number", "expiry_date", "quantity_available", "supplier"]
    list_filter = ["hospital"]
    search_fields = ["batch_number", "medicine__name"]


@admin.register(DispenseRecord)
class DispenseRecordAdmin(admin.ModelAdmin):
    list_display = ["batch", "quantity", "dispensed_by", "dispensed_at"]
    list_filter = ["hospital"]


@admin.register(StockAdjustment)
class StockAdjustmentAdmin(admin.ModelAdmin):
    list_display = ["batch", "adjustment_type", "quantity_delta", "adjusted_at"]
    list_filter = ["hospital", "adjustment_type"]
