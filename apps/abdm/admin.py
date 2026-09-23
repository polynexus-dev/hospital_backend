from django.contrib import admin

from .models import AbhaLink, ConsentRequest, HealthRecordFetch, NHCXTransaction


@admin.register(AbhaLink)
class AbhaLinkAdmin(admin.ModelAdmin):
    list_display = ["patient", "status", "verification_method", "hospital", "initiated_at", "linked_at"]
    list_filter = ["hospital", "status", "verification_method"]


@admin.register(ConsentRequest)
class ConsentRequestAdmin(admin.ModelAdmin):
    list_display = ["patient", "purpose", "status", "hospital", "requested_at", "expires_at"]
    list_filter = ["hospital", "purpose", "status"]


@admin.register(HealthRecordFetch)
class HealthRecordFetchAdmin(admin.ModelAdmin):
    list_display = ["consent_request", "fetched_by", "fetched_at", "record_count", "hospital"]
    list_filter = ["hospital"]


@admin.register(NHCXTransaction)
class NHCXTransactionAdmin(admin.ModelAdmin):
    list_display = ["transaction_type", "status", "preauth_request", "hospital", "initiated_at"]
    list_filter = ["hospital", "transaction_type", "status"]
