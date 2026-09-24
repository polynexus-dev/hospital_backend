from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("ambulances", views.AmbulanceViewSet, basename="ambulance")
router.register("ambulance-trips", views.AmbulanceTripViewSet, basename="ambulancetrip")
router.register("instrument-sets", views.InstrumentSetViewSet, basename="instrumentset")
router.register("sterilization-cycles", views.SterilizationCycleViewSet, basename="sterilizationcycle")
router.register("sterile-batches", views.SterileBatchViewSet, basename="sterilebatch")
router.register("housekeeping-tasks", views.HousekeepingTaskViewSet, basename="housekeepingtask")
router.register("equipment", views.EquipmentAssetViewSet, basename="equipmentasset")
router.register("maintenance", views.MaintenanceRecordViewSet, basename="maintenancerecord")

from . import laundry_views  # noqa: E402

router.register("linen-types", laundry_views.LinenTypeViewSet, basename="linentype")
router.register("linen-stock", laundry_views.LinenStockViewSet, basename="linenstock")
router.register("laundry-batches", laundry_views.LaundryBatchViewSet, basename="laundrybatch")

urlpatterns = router.urls + [
    path("ambulance-device/feed/", views.AmbulanceDeviceFeedView.as_view(), name="ambulance-device-feed"),
]
