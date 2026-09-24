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

from .workflow import (  # noqa: E402
    EmergencyMedicationStockViewSet,
    MedicationReconciliationViewSet,
    MedicineRecallViewSet,
    MedicineReturnViewSet,
    PharmacyIndentViewSet,
    StockOutEventViewSet,
)

router.register("returns", MedicineReturnViewSet, basename="medicinereturn")
router.register("recalls", MedicineRecallViewSet, basename="medicinerecall")
router.register("reconciliations", MedicationReconciliationViewSet, basename="medicationreconciliation")
router.register("indents", PharmacyIndentViewSet, basename="pharmacyindent")
router.register("emergency-stock", EmergencyMedicationStockViewSet, basename="emergencymedicationstock")
router.register("stock-outs", StockOutEventViewSet, basename="stockoutevent")
urlpatterns = router.urls
