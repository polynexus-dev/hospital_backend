from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    AppointmentViewSet,
    DoctorViewSet,
    PaperlessRegistrationView,
    SlotTemplateViewSet,
    SlotViewSet,
    WaitlistViewSet,
)

router = DefaultRouter()
router.register("doctors", DoctorViewSet, basename="doctor")
router.register("slot-templates", SlotTemplateViewSet, basename="slottemplate")
router.register("slots", SlotViewSet, basename="slot")
router.register("appointments", AppointmentViewSet, basename="appointment")
router.register("waitlist", WaitlistViewSet, basename="waitlist")

urlpatterns = router.urls + [
    path("registration/<str:token>/", PaperlessRegistrationView.as_view(), name="paperless-registration"),
]

from django.urls import path as _path  # noqa: E402

from .calendar_views import (  # noqa: E402
    CalendarCallbackView,
    CalendarConnectionDetailView,
    CalendarConnectionsView,
    CalendarConnectView,
    CalendarSyncNowView,
)
from .views import ConsultationTimeView, DoctorScheduleView  # noqa: E402

urlpatterns = [
    _path("calendar/connections/", CalendarConnectionsView.as_view(), name="calendar-connections"),
    _path("calendar/connections/<int:pk>/", CalendarConnectionDetailView.as_view(), name="calendar-connection"),
    _path("calendar/connections/<int:pk>/sync/", CalendarSyncNowView.as_view(), name="calendar-sync"),
    _path("calendar/connect/", CalendarConnectView.as_view(), name="calendar-connect"),
    _path("calendar/callback/<str:provider>/", CalendarCallbackView.as_view(), name="calendar-callback"),
    _path("doctors/<int:pk>/schedule/", DoctorScheduleView.as_view(), name="doctor-schedule"),
    _path("consultation-time/", ConsultationTimeView.as_view(), name="consultation-time"),
] + list(urlpatterns)
