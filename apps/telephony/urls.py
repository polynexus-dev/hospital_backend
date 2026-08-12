from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import CallbackTaskViewSet, CallViewSet, IVRRouteViewSet, TelephonyWebhookView

router = DefaultRouter()
router.register("calls", CallViewSet, basename="call")
router.register("callback-tasks", CallbackTaskViewSet, basename="callbacktask")
router.register("ivr-routes", IVRRouteViewSet, basename="ivrroute")

urlpatterns = router.urls + [
    path("webhooks/telephony/<uuid:hospital_id>/", TelephonyWebhookView.as_view(), name="telephony-webhook"),
]
