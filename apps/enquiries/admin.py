from django.contrib import admin

from .models import Enquiry, EnquiryStageChange


class StageChangeInline(admin.TabularInline):
    model = EnquiryStageChange
    extra = 0
    readonly_fields = ["from_stage", "to_stage", "changed_by", "created_at"]
    can_delete = False


@admin.register(Enquiry)
class EnquiryAdmin(admin.ModelAdmin):
    list_display = ["name", "mobile", "hospital", "source", "stage", "department", "assigned_to", "urgency"]
    list_filter = ["hospital", "stage", "source", "department", "urgency"]
    search_fields = ["name", "mobile", "alternate_mobile", "email"]
    inlines = [StageChangeInline]
