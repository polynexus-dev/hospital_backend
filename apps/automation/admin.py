from django.contrib import admin

from .models import EscalationRule, Task


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ["title", "hospital", "owner", "department", "status", "priority", "due_at"]
    list_filter = ["hospital", "status", "priority", "department"]
    search_fields = ["title"]


@admin.register(EscalationRule)
class EscalationRuleAdmin(admin.ModelAdmin):
    list_display = ["name", "applies_to", "department", "escalate_after_minutes", "escalate_to", "is_active"]
    list_filter = ["hospital", "applies_to", "is_active"]
