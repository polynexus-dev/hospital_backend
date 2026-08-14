from rest_framework.routers import DefaultRouter

from .views import (
    DispenseRecordViewSet,
    MedicineBatchViewSet,
    MedicineViewSet,
    StockAdjustmentViewSet,
    SupplierViewSet,
)

router = DefaultRouter()
router.register("suppliers", SupplierViewSet, basename="supplier")
router.register("medicines", MedicineViewSet, basename="medicine")
router.register("batches", MedicineBatchViewSet, basename="medicinebatch")
router.register("dispense-records", DispenseRecordViewSet, basename="dispenserecord")
router.register("stock-adjustments", StockAdjustmentViewSet, basename="stockadjustment")

urlpatterns = router.urls
