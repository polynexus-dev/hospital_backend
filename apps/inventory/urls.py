from rest_framework.routers import DefaultRouter
from .views import ItemCategoryViewSet, ItemViewSet, PurchaseOrderViewSet, StockLevelViewSet, StockTransactionViewSet

router = DefaultRouter()
router.register(r"categories", ItemCategoryViewSet, basename="itemcategory")
router.register(r"items", ItemViewSet, basename="item")
router.register(r"stock-levels", StockLevelViewSet, basename="stocklevel")
router.register(r"purchase-orders", PurchaseOrderViewSet, basename="purchaseorder")
router.register(r"stock-transactions", StockTransactionViewSet, basename="stocktransaction")

urlpatterns = router.urls
