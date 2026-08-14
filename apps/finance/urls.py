from rest_framework.routers import DefaultRouter
from .views import ExpenseViewSet, LedgerViewSet, ReceivableViewSet

router = DefaultRouter()
router.register(r"ledger", LedgerViewSet, basename="ledger")
router.register(r"expenses", ExpenseViewSet, basename="expense")
router.register(r"receivables", ReceivableViewSet, basename="receivable")

urlpatterns = router.urls
