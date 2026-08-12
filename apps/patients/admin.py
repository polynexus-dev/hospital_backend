from django.contrib import admin

from .models import Document, Patient, TimelineEvent


class DocumentInline(admin.TabularInline):
    model = Document
    extra = 0
    fields = ["category", "title", "file", "uploaded_by"]
    readonly_fields = ["uploaded_by"]


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ["full_name", "mobile", "hospital", "city", "preferred_language", "is_active"]
    list_filter = ["hospital", "gender", "preferred_language", "is_active"]
    search_fields = ["first_name", "last_name", "mobile", "alternate_mobile", "email"]
    inlines = [DocumentInline]


@admin.register(TimelineEvent)
class TimelineEventAdmin(admin.ModelAdmin):
    list_display = ["patient", "event_type", "summary", "occurred_at"]
    list_filter = ["event_type", "hospital"]
    search_fields = ["summary"]
