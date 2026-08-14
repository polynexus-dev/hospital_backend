from django.contrib import admin

from .models import ConsentOptOut, Message, Template


@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    list_display = ["name", "purpose", "channel", "language", "hospital", "is_active"]
    list_filter = ["hospital", "channel", "language", "is_active"]
    search_fields = ["name", "purpose"]


@admin.register(ConsentOptOut)
class ConsentOptOutAdmin(admin.ModelAdmin):
    list_display = ["patient", "channel", "purpose", "is_opted_out"]
    list_filter = ["hospital", "channel", "purpose", "is_opted_out"]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ["patient", "enquiry", "channel", "direction", "status", "created_at"]
    list_filter = ["hospital", "channel", "direction", "status"]
    search_fields = ["patient__first_name", "patient__last_name", "body"]
