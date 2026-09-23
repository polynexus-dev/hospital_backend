from django.contrib import admin

from .models import Document, Patient, TimelineEvent


class DocumentInline(admin.TabularInline):
    model = Document
    extra = 0
    fields = ["category", "title", "file", "uploaded_by"]
    readonly_fields = ["uploaded_by"]


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ["full_name", "mobile", "hospital", "city", "guardian", "next_recall_due_at", "preferred_language", "is_active", "is_deleted"]
    list_filter = ["hospital", "gender", "preferred_language", "is_active", "is_deleted"]
    # mobile/alternate_mobile are encrypted at rest (Part A #2) and excluded
    # here — admin search does an icontains against the DB column, which
    # matches nothing against ciphertext.
    search_fields = ["first_name", "last_name", "email"]
    inlines = [DocumentInline]

    def get_queryset(self, request):
        # Soft-deleted patients (Part A #1) are invisible through the
        # default manager — surface them here too, since this is the one
        # place ops staff would go looking for a "deleted" record.
        return Patient.objects.all_with_deleted()


@admin.register(TimelineEvent)
class TimelineEventAdmin(admin.ModelAdmin):
    list_display = ["patient", "event_type", "summary", "occurred_at"]
    list_filter = ["event_type", "hospital"]
    search_fields = ["summary"]
