from django.urls import path
from rest_framework.routers import DefaultRouter

from . import auth, views

router = DefaultRouter()

urlpatterns = [
    # Auth
    path("auth/otp/send/", auth.SendOTPView.as_view(), name="otp-send"),
    path("auth/otp/verify/", auth.VerifyOTPView.as_view(), name="otp-verify"),
    path("auth/refresh/", auth.TokenRefreshView.as_view(), name="token-refresh"),
    path("auth/logout/", auth.LogoutView.as_view(), name="logout"),
    path("devices/", auth.DeviceRegistrationView.as_view(), name="device-register"),

    # Profile & Family
    path("me/", views.ProfileView.as_view(), name="profile"),
    path("me/family/", views.FamilyView.as_view(), name="family-list"),
    path("me/family/<int:pk>/", views.FamilyDetailView.as_view(), name="family-detail"),
    path("me/preferences/", views.PreferencesView.as_view(), name="preferences"),

    # Hospitals & Doctors
    path("hospitals/", views.HospitalListView.as_view(), name="hospital-list"),
    path("hospitals/<int:pk>/", views.HospitalDetailView.as_view(), name="hospital-detail"),
    path("departments/", views.DepartmentListView.as_view(), name="department-list"),
    path("doctors/", views.DoctorListView.as_view(), name="doctor-list"),
    path("doctors/<int:pk>/", views.DoctorDetailView.as_view(), name="doctor-detail"),
    path("doctors/<int:pk>/slots/", views.DoctorSlotsView.as_view(), name="doctor-slots"),

    # Appointments
    path("appointments/", views.AppointmentListView.as_view(), name="appointment-list"),
    path("appointments/<int:pk>/", views.AppointmentDetailView.as_view(), name="appointment-detail"),
    path("appointments/<int:pk>/reschedule/", views.AppointmentRescheduleView.as_view(), name="appointment-reschedule"),
    path("appointments/<int:pk>/cancel/", views.AppointmentCancelView.as_view(), name="appointment-cancel"),
    path("appointments/<int:pk>/queue/", views.AppointmentQueueView.as_view(), name="appointment-queue"),
    path("appointments/<int:pk>/checkin/", views.AppointmentCheckinView.as_view(), name="appointment-checkin"),

    # Payments & Billing
    path("payments/initiate/", views.PaymentInitiateView.as_view(), name="payment-initiate"),
    path("payments/verify/", views.PaymentVerifyView.as_view(), name="payment-verify"),
    path("payments/webhook/", views.PaymentWebhookView.as_view(), name="payment-webhook"),
    path("bills/", views.BillListView.as_view(), name="bill-list"),
    path("bills/<int:pk>/", views.BillDetailView.as_view(), name="bill-detail"),
    path("bills/<int:pk>/receipt/", views.BillReceiptView.as_view(), name="bill-receipt"),

    # Medical Records
    path("prescriptions/", views.PrescriptionListView.as_view(), name="prescription-list"),
    path("prescriptions/<int:pk>/", views.PrescriptionDetailView.as_view(), name="prescription-detail"),
    path("reports/", views.ReportListView.as_view(), name="report-list"),
    path("reports/<int:pk>/file/", views.ReportFileView.as_view(), name="report-file"),
    path("visits/", views.VisitListView.as_view(), name="visit-list"),
    path("documents/", views.DocumentListView.as_view(), name="document-list"),

    # Packages & Camps
    path("packages/", views.PackageListView.as_view(), name="package-list"),
    path("packages/<int:pk>/", views.PackageDetailView.as_view(), name="package-detail"),
    path("packages/<int:pk>/book/", views.PackageBookView.as_view(), name="package-book"),
    path("camps/", views.CampListView.as_view(), name="camp-list"),
    path("camps/<int:pk>/register/", views.CampRegisterView.as_view(), name="camp-register"),

    # Teleconsult
    path("teleconsult/sessions/", views.TeleconsultSessionView.as_view(), name="teleconsult-session"),
    path("teleconsult/sessions/<int:pk>/token/", views.TeleconsultTokenView.as_view(), name="teleconsult-token"),

    # Support & Engagement
    path("callbacks/", views.CallbackRequestView.as_view(), name="callback-request"),
    path("notifications/", views.NotificationListView.as_view(), name="notification-list"),
    path("notifications/<int:pk>/read/", views.NotificationReadView.as_view(), name="notification-read"),
    path("feedback/", views.FeedbackView.as_view(), name="feedback"),
    path("assistant/message/", views.AssistantMessageView.as_view(), name="assistant-message"),

    # Insurance
    path("insurance/", views.InsuranceView.as_view(), name="insurance-list"),
    path("preauth/<int:pk>/status/", views.PreauthStatusView.as_view(), name="preauth-status"),
]
