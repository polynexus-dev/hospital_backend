from django.contrib import admin

from .models import HISBillingRecord, HISVisit


@admin.register(HISVisit)
class HISVisitAdmin(admin.ModelAdmin):
    list_display = ["patient", "visit_type", "visit_date", "department_name", "doctor_name"]
    list_filter = ["hospital", "visit_type"]


@admin.register(HISBillingRecord)
class HISBillingRecordAdmin(admin.ModelAdmin):
    list_display = ["patient", "bill_date", "total_amount", "paid_amount", "status"]
    list_filter = ["hospital", "status"]
