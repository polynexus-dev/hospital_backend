from rest_framework.routers import DefaultRouter

from .views import FieldVisitViewSet, ReferralRecordViewSet, ReferringDoctorViewSet

router = DefaultRouter()
router.register("doctors", ReferringDoctorViewSet, basename="referringdoctor")
router.register("records", ReferralRecordViewSet, basename="referralrecord")
router.register("field-visits", FieldVisitViewSet, basename="fieldvisit")

urlpatterns = router.urls
