from rest_framework.routers import DefaultRouter

from .views import BloodUnitViewSet, CrossMatchRequestViewSet, DonorViewSet, TransfusionViewSet

router = DefaultRouter()
router.register(r"donors", DonorViewSet, basename="donor")
router.register(r"units", BloodUnitViewSet, basename="bloodunit")
router.register(r"cross-matches", CrossMatchRequestViewSet, basename="crossmatchrequest")
router.register(r"transfusions", TransfusionViewSet, basename="transfusion")

urlpatterns = router.urls

from django.urls import path  # noqa: E402

from .views import BloodStockSummaryViewSet  # noqa: E402
from .workflow import PublicBloodStockView  # noqa: E402

router.register("stock-summary", BloodStockSummaryViewSet, basename="bloodstocksummary")
urlpatterns = router.urls + [path("public/stock/", PublicBloodStockView.as_view(), name="public-blood-stock")]
