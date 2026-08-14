from rest_framework.routers import DefaultRouter

from .views import (
    AdmissionViewSet,
    BedAllocationViewSet,
    DischargeSummaryViewSet,
    DoctorProgressNoteViewSet,
    WardTransferViewSet,
)

router = DefaultRouter()
router.register("admissions", AdmissionViewSet, basename="admission")
router.register("bed-allocations", BedAllocationViewSet, basename="bedallocation")
router.register("ward-transfers", WardTransferViewSet, basename="wardtransfer")
router.register("progress-notes", DoctorProgressNoteViewSet, basename="doctorprogressnote")
router.register("discharge-summaries", DischargeSummaryViewSet, basename="dischargesummary")

urlpatterns = router.urls
