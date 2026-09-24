from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("admin/care-information", views.CareInformationViewSet, basename="careinformation")
router.register("admin/reported-measures", views.PatientReportedMeasureViewSet, basename="patientreportedmeasure")

urlpatterns = router.urls + [
    path("auth/request-otp/", views.RequestOTPView.as_view()),
    path("auth/verify-otp/", views.VerifyOTPView.as_view()),
    path("me/", views.MeView.as_view()),
    path("doctors/", views.DoctorsView.as_view()),
    path("doctors/<int:doctor_id>/slots/", views.SlotsView.as_view()),
    path("appointments/", views.AppointmentsView.as_view()),
    path("reports/", views.ReportsView.as_view()),
    path("reports/lab/<int:order_id>/pdf/", views.LabReportPDFView.as_view()),
    path("prescriptions/", views.PrescriptionsView.as_view()),
    path("prescriptions/<int:rx_id>/pdf/", views.PrescriptionPDFView.as_view()),
    path("discharge-summaries/", views.DischargeSummariesView.as_view()),
    path("bills/", views.BillsView.as_view()),
    path("teleconsultations/", views.TeleconsultationsView.as_view()),
    path("care-information/", views.CareInformationView.as_view()),
    path("complaints/", views.ComplaintView.as_view()),
    path("measures/", views.MeasuresView.as_view()),
]
