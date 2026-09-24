from rest_framework.routers import DefaultRouter

from .views import CathProcedureViewSet

router = DefaultRouter()
router.register("procedures", CathProcedureViewSet, basename="cathprocedure")

urlpatterns = router.urls
