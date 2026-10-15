from rest_framework.routers import DefaultRouter

from .views import ClaimViewSet, PreAuthRequestViewSet, TPACompanyViewSet

router = DefaultRouter()
router.register("companies", TPACompanyViewSet, basename="tpacompany")
router.register("pre-auth", PreAuthRequestViewSet, basename="preauthrequest")
router.register("claims", ClaimViewSet, basename="claim")

from django.urls import path  # noqa: E402

from . import inbox_views  # noqa: E402

router.register("messages", inbox_views.ClaimMessageViewSet, basename="claimmessage")
router.register("non-payable-lists", inbox_views.NonPayableListViewSet, basename="nonpayablelist")
router.register("non-payable-items", inbox_views.NonPayableItemViewSet, basename="nonpayableitem")

urlpatterns = router.urls + [
    path("nhcx-profile/", inbox_views.NHCXProfileView.as_view(), name="nhcx-profile"),
    path("mailbox/", inbox_views.TPAMailboxView.as_view(), name="tpa-mailbox"),
]
