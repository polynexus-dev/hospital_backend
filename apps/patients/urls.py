from rest_framework.routers import DefaultRouter

from .views import DocumentViewSet, PatientViewSet, PrescriptionViewSet

router = DefaultRouter()
router.register("patients", PatientViewSet, basename="patient")
router.register("documents", DocumentViewSet, basename="document")
router.register("prescriptions", PrescriptionViewSet, basename="prescription")

urlpatterns = router.urls

