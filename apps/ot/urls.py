from rest_framework.routers import DefaultRouter

from .views import (
    AnaesthesiaRecordViewSet,
    ConsumableUsageViewSet,
    ImplantUsageViewSet,
    OperativeNoteViewSet,
    OTScheduleViewSet,
    PreOpChecklistViewSet,
    SurgeryRequestViewSet,
)

router = DefaultRouter()
router.register(r"surgery-requests", SurgeryRequestViewSet, basename="surgeryrequest")
router.register(r"schedules", OTScheduleViewSet, basename="otschedule")
router.register(r"preop-checklists", PreOpChecklistViewSet, basename="preopchecklist")
router.register(r"operative-notes", OperativeNoteViewSet, basename="operativenote")
router.register(r"anaesthesia-records", AnaesthesiaRecordViewSet, basename="anaesthesiarecord")
router.register(r"consumables", ConsumableUsageViewSet, basename="consumableusage")
router.register(r"implants", ImplantUsageViewSet, basename="implantusage")

urlpatterns = router.urls
