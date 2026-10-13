from django.contrib import admin

from .models import Enquiry, EnquiryAssignmentChange, EnquiryStageChange


class StageChangeInline(admin.TabularInline):
    model = EnquiryStageChange
    extra = 0
    readonly_fields = ["from_stage", "to_stage", "changed_by", "created_at"]
    can_delete = False


class AssignmentChangeInline(admin.TabularInline):
    model = EnquiryAssignmentChange
    extra = 0
    readonly_fields = ["from_owner", "to_owner", "changed_by", "reason", "created_at"]
    can_delete = False


@admin.register(Enquiry)
class EnquiryAdmin(admin.ModelAdmin):
    list_display = ["name", "mobile", "hospital", "source", "stage", "department", "assigned_to", "urgency", "score"]
    list_filter = ["hospital", "stage", "source", "department", "urgency", "lost_reason"]
    search_fields = ["utm_campaign"]  # name and contact details are encrypted; search them in the app
    inlines = [StageChangeInline, AssignmentChangeInline]
