from django.urls import path
from rest_framework.routers import DefaultRouter

from .ivr import IVREventView, IVRHoldView, IVRStepView
from .views import (
    CallbackTaskViewSet,
    CallViewSet,
    IVRMenuViewSet,
    IVRNodeViewSet,
    IVRRouteViewSet,
    LiveQueueView,
    TelephonyWebhookView,
    WebRTCConfigView,
)

router = DefaultRouter()
router.register("calls", CallViewSet, basename="call")
router.register("callback-tasks", CallbackTaskViewSet, basename="callbacktask")
router.register("ivr-routes", IVRRouteViewSet, basename="ivrroute")
router.register("ivr-menus", IVRMenuViewSet, basename="ivrmenu")
router.register("ivr-nodes", IVRNodeViewSet, basename="ivrnode")

urlpatterns = router.urls + [
    path("webhooks/telephony/<uuid:hospital_id>/", TelephonyWebhookView.as_view(), name="telephony-webhook"),
    path("telephony/webrtc-config/", WebRTCConfigView.as_view(), name="webrtc-config"),
    path("call-queue/", LiveQueueView.as_view(), name="call-queue"),
    path("ivr/<uuid:token>/step/", IVRStepView.as_view(), name="ivr-step"),
    path("ivr/<uuid:token>/hold/", IVRHoldView.as_view(), name="ivr-hold"),
    path("ivr/<uuid:token>/event/", IVREventView.as_view(), name="ivr-event"),
]
