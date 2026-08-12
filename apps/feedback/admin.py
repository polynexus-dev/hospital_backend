from django.contrib import admin

from .models import Complaint, FeedbackRequest, NPSResponse, ServiceRecoveryTask


@admin.register(FeedbackRequest)
class FeedbackRequestAdmin(admin.ModelAdmin):
    list_display = ["patient", "doctor", "status", "sent_at"]
    list_filter = ["hospital", "status", "doctor"]


@admin.register(NPSResponse)
class NPSResponseAdmin(admin.ModelAdmin):
    list_display = ["patient", "score", "category", "doctor", "department", "created_at"]
    list_filter = ["hospital", "category", "doctor", "department"]


@admin.register(Complaint)
class ComplaintAdmin(admin.ModelAdmin):
    list_display = ["patient", "department", "status", "owner"]
    list_filter = ["hospital", "status", "department"]


@admin.register(ServiceRecoveryTask)
class ServiceRecoveryTaskAdmin(admin.ModelAdmin):
    list_display = ["nps_response", "status", "owner", "sla_due_at"]
    list_filter = ["hospital", "status"]
