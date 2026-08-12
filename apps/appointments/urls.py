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
