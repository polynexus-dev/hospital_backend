from rest_framework.routers import DefaultRouter

from .views import RadiologyOrderViewSet, RadiologyProcedureViewSet, RadiologyReportViewSet

router = DefaultRouter()
router.register("procedures", RadiologyProcedureViewSet, basename="radiologyprocedure")
router.register("orders", RadiologyOrderViewSet, basename="radiologyorder")
router.register("reports", RadiologyReportViewSet, basename="radiologyreport")

urlpatterns = router.urls
