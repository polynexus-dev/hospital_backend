from rest_framework.routers import DefaultRouter

from .views import RadiologyOrderViewSet, RadiologyProcedureViewSet, RadiologyReportViewSet

router = DefaultRouter()
router.register("procedures", RadiologyProcedureViewSet, basename="radiologyprocedure")
router.register("orders", RadiologyOrderViewSet, basename="radiologyorder")
router.register("reports", RadiologyReportViewSet, basename="radiologyreport")

urlpatterns = router.urls

from .workflow import RadiologyAppointmentViewSet, RadiologyEquipmentViewSet, RadiologyTemplateViewSet  # noqa: E402

router.register("templates", RadiologyTemplateViewSet, basename="radiologytemplate")
router.register("equipment", RadiologyEquipmentViewSet, basename="radiologyequipment")
router.register("appointments", RadiologyAppointmentViewSet, basename="radiologyappointment")
urlpatterns = router.urls
