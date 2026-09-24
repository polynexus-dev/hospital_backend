from rest_framework.routers import DefaultRouter
from .views import ItemCategoryViewSet, ItemViewSet, PurchaseOrderViewSet, StockLevelViewSet, StockTransactionViewSet

router = DefaultRouter()
router.register(r"categories", ItemCategoryViewSet, basename="itemcategory")
router.register(r"items", ItemViewSet, basename="item")
router.register(r"stock-levels", StockLevelViewSet, basename="stocklevel")
router.register(r"purchase-orders", PurchaseOrderViewSet, basename="purchaseorder")
router.register(r"stock-transactions", StockTransactionViewSet, basename="stocktransaction")

from .procurement import (  # noqa: E402
    GoodsReceiptNoteViewSet,
    PurchaseApprovalRuleViewSet,
    StockTransferViewSet,
    StoreIndentViewSet,
    StoreViewSet,
    SupplierRatingViewSet,
)

router.register(r"stores", StoreViewSet, basename="store")
router.register(r"approval-rules", PurchaseApprovalRuleViewSet, basename="purchaseapprovalrule")
router.register(r"indents", StoreIndentViewSet, basename="storeindent")
router.register(r"grns", GoodsReceiptNoteViewSet, basename="goodsreceiptnote")
router.register(r"transfers", StockTransferViewSet, basename="stocktransfer")
router.register(r"supplier-ratings", SupplierRatingViewSet, basename="supplierrating")
urlpatterns = router.urls
