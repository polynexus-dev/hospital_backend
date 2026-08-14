from rest_framework.routers import DefaultRouter

from .views import EDVisitViewSet, TriageViewSet

router = DefaultRouter()
router.register(r"ed-visits", EDVisitViewSet, basename="edvisit")
router.register(r"triages", TriageViewSet, basename="triage")

urlpatterns = router.urls
