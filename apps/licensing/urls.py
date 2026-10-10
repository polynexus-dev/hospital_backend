from django.urls import path

from .views import LicenseStatusView, LicenseUploadView, UsageReportView

urlpatterns = [
    path("licensing/status/", LicenseStatusView.as_view(), name="license-status"),
    path("licensing/upload/", LicenseUploadView.as_view(), name="license-upload"),
    path("licensing/usage-report/", UsageReportView.as_view(), name="license-usage-report"),
]
