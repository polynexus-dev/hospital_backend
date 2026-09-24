from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("service-points", views.ServicePointViewSet, basename="servicepoint")
router.register("tokens", views.QueueTokenViewSet, basename="queuetoken")
router.register("displays", views.QueueDisplayViewSet, basename="queuedisplay")

urlpatterns = router.urls + [
    path("public/board/<uuid:key>/", views.PublicQueueBoardView.as_view(), name="public-queue-board"),
]
