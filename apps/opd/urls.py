from rest_framework.routers import DefaultRouter

from .views import (
    ClinicalNoteViewSet,
    DiagnosisViewSet,
    EncounterViewSet,
    InvestigationOrderViewSet,
    VitalsReadingViewSet,
)

router = DefaultRouter()
router.register("encounters", EncounterViewSet, basename="encounter")
router.register("vitals", VitalsReadingViewSet, basename="vitalsreading")
router.register("clinical-notes", ClinicalNoteViewSet, basename="clinicalnote")
router.register("diagnoses", DiagnosisViewSet, basename="diagnosis")
router.register("investigation-orders", InvestigationOrderViewSet, basename="investigationorder")

urlpatterns = router.urls
