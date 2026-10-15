from .consumption import InventoryConsumptionView, InventoryTransferRequestView
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

from django.urls import path  # noqa: E402

from . import contract_views  # noqa: E402

router.register(r"rate-contracts", contract_views.RateContractViewSet, basename="ratecontract")
router.register(r"rate-contract-lines", contract_views.RateContractLineViewSet, basename="ratecontractline")
urlpatterns = router.urls + [
    path("cart/<str:kind>/", contract_views.CartView.as_view(), name="procurement-cart"),
    path("consumption/", InventoryConsumptionView.as_view(), name="inventory-consumption"),
    path("consumption/request/", InventoryTransferRequestView.as_view(), name="inventory-transfer-request"),
    path("cart/<str:kind>/submit/", contract_views.CartSubmitView.as_view(), name="procurement-cart-submit"),
]
