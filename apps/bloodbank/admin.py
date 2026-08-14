from django.contrib import admin

from .models import BloodUnit, CrossMatchRequest, Donor, Transfusion


@admin.register(Donor)
class DonorAdmin(admin.ModelAdmin):
    list_display = ["id", "hospital", "name", "blood_group", "phone", "last_donation_date"]
    list_filter = ["blood_group", "hospital"]


@admin.register(BloodUnit)
class BloodUnitAdmin(admin.ModelAdmin):
    list_display = ["id", "blood_group", "component", "collection_date", "expiry_date", "status"]
    list_filter = ["blood_group", "component", "status"]


@admin.register(CrossMatchRequest)
class CrossMatchRequestAdmin(admin.ModelAdmin):
    list_display = ["id", "patient", "blood_group_required", "component", "status"]
    list_filter = ["status", "component"]


@admin.register(Transfusion)
class TransfusionAdmin(admin.ModelAdmin):
    list_display = ["id", "blood_unit", "patient", "transfused_at"]
