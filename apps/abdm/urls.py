from django.urls import path, re_path
from rest_framework.routers import DefaultRouter

from .callbacks import DataPushView, GatewayCallbackView
from .views import (
    AbhaLinkViewSet, ABDMFacilityViewSet, CareContextViewSet, ConsentArtefactViewSet, ConsentRequestViewSet, DataTransferViewSet,
    HealthRecordFetchViewSet, NHCXTransactionViewSet, ReceivedHealthRecordViewSet,
)

router = DefaultRouter()
router.register(r"abha-links", AbhaLinkViewSet, basename="abhalink")
router.register(r"consent-requests", ConsentRequestViewSet, basename="consentrequest")
router.register(r"health-record-fetches", HealthRecordFetchViewSet, basename="healthrecordfetch")
router.register(r"nhcx-transactions", NHCXTransactionViewSet, basename="nhcxtransaction")
router.register(r"facility", ABDMFacilityViewSet, basename="abdmfacility")
router.register(r"care-contexts", CareContextViewSet, basename="carecontext")
router.register(r"consent-artefacts", ConsentArtefactViewSet, basename="consentartefact")
router.register(r"data-transfers", DataTransferViewSet, basename="datatransfer")
router.register(r"received-records", ReceivedHealthRecordViewSet, basename="receivedhealthrecord")

urlpatterns = router.urls + [
    # Called by ABDM's gateway (bridge URL: https://<host>/api/v1/abdm/callbacks).
    re_path(r"^callbacks/api/v3/(?P<path>[a-z_/-]+)$", GatewayCallbackView.as_view(), name="abdm-gateway-callback"),
    path("data-push/", DataPushView.as_view(), name="abdm-data-push"),
]
