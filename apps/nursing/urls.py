from rest_framework.routers import DefaultRouter

from .views import IntakeOutputViewSet, MedicationAdministrationViewSet, NursingNoteViewSet

router = DefaultRouter()
router.register("notes", NursingNoteViewSet, basename="nursingnote")
router.register("medication-administrations", MedicationAdministrationViewSet, basename="medicationadministration")
router.register("intake-output", IntakeOutputViewSet, basename="intakeoutput")

urlpatterns = router.urls
