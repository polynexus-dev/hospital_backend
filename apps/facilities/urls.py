from rest_framework.routers import DefaultRouter

from .views import BedViewSet, RoomViewSet, WardViewSet

router = DefaultRouter()
router.register("wards", WardViewSet, basename="ward")
router.register("rooms", RoomViewSet, basename="room")
router.register("beds", BedViewSet, basename="bed")

urlpatterns = router.urls
