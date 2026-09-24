from rest_framework.routers import DefaultRouter

from django.urls import path

from .registration import UHIDConfigView
from .views import DocumentViewSet, PatientViewSet, PrescriptionViewSet

router = DefaultRouter()
router.register("patients", PatientViewSet, basename="patient")
router.register("documents", DocumentViewSet, basename="document")
router.register("prescriptions", PrescriptionViewSet, basename="prescription")

urlpatterns = [path("patients/uhid-config/", UHIDConfigView.as_view(), name="uhid-config")] + router.urls

