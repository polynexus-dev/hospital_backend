from rest_framework.routers import DefaultRouter

from .views import DataRightsRequestViewSet, GrievanceTicketViewSet, NomineeViewSet

router = DefaultRouter()
router.register(r"data-rights-requests", DataRightsRequestViewSet, basename="datarightsrequest")
router.register(r"grievances", GrievanceTicketViewSet, basename="grievanceticket")
router.register(r"nominees", NomineeViewSet, basename="nominee")

urlpatterns = router.urls
