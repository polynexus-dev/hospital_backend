from django.contrib import admin

from .models import Claim, PreAuthRequest, TPACompany


@admin.register(TPACompany)
class TPACompanyAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "hospital", "avg_tat_days", "is_active"]
    list_filter = ["hospital", "is_active"]
    search_fields = ["name", "code"]


@admin.register(PreAuthRequest)
class PreAuthRequestAdmin(admin.ModelAdmin):
    list_display = ["patient", "tpa_company", "policy_number", "claim_amount", "approved_amount", "status", "submitted_at"]
    list_filter = ["hospital", "status", "tpa_company"]
    search_fields = ["policy_number", "patient__first_name", "patient__last_name"]


@admin.register(Claim)
class ClaimAdmin(admin.ModelAdmin):
    list_display = ["patient", "tpa_company", "claim_number", "billed_amount", "settled_amount", "status", "submitted_at"]
    list_filter = ["hospital", "status", "tpa_company"]
    search_fields = ["claim_number", "patient__first_name", "patient__last_name"]
