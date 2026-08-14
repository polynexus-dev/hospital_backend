from rest_framework.routers import DefaultRouter

from .views import ICUAdmissionViewSet, ICUDailyProgressNoteViewSet, VentilatorLogViewSet

router = DefaultRouter()
router.register(r"admissions", ICUAdmissionViewSet, basename="icuadmission")
router.register(r"ventilator-logs", VentilatorLogViewSet, basename="ventilatorlog")
router.register(r"progress-notes", ICUDailyProgressNoteViewSet, basename="icudailyprogressnote")

urlpatterns = router.urls
