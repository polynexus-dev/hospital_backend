from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views, views_extra

router = DefaultRouter()
router.register("episodes", views.EpisodeOfCareViewSet, basename="episode")
router.register("allergies", views.AllergyViewSet, basename="allergy")
router.register("assessment-templates", views.AssessmentTemplateViewSet, basename="assessmenttemplate")
router.register("assessments", views.ClinicalAssessmentViewSet, basename="clinicalassessment")
router.register("risk-assessments", views.RiskAssessmentViewSet, basename="riskassessment")
router.register("order-sets", views.OrderSetViewSet, basename="orderset")
router.register("consents", views.ConsentRecordViewSet, basename="consent")
router.register("handovers", views.ShiftHandoverViewSet, basename="handover")
router.register("care-plans", views.CarePlanViewSet, basename="careplan")
router.register("drug-interactions", views.DrugInteractionViewSet, basename="druginteraction")
router.register("drug-condition-rules", views.DrugConditionRuleViewSet, basename="drugconditionrule")
router.register("alerts", views.ClinicalAlertViewSet, basename="clinicalalert")
router.register("notifiable-diseases", views.NotifiableDiseaseViewSet, basename="notifiabledisease")
router.register("notifiable-reports", views.NotifiableDiseaseReportViewSet, basename="notifiablereport")
router.register("result-reviews", views.ResultReviewViewSet, basename="resultreview")
router.register("homecare-services", views.HomecareServiceViewSet, basename="homecareservice")
router.register("homecare-bookings", views.HomecareBookingViewSet, basename="homecarebooking")
router.register("functional-assessments", views.FunctionalAssessmentViewSet, basename="functionalassessment")
router.register("referrals", views_extra.SpecialtyReferralViewSet, basename="specialtyreferral")
router.register("record-shares", views_extra.RecordShareViewSet, basename="recordshare")
router.register("devices", views_extra.MedicalDeviceViewSet, basename="medicaldevice")
router.register("device-vitals", views_extra.DeviceVitalSignViewSet, basename="devicevitalsign")

urlpatterns = router.urls + [
    path("cdss/check/", views.CDSSCheckView.as_view(), name="cdss-check"),
    path("shared-with-me/", views_extra.SharedWithMeView.as_view(), name="shared-with-me"),
    path("shared-with-me/<int:share_id>/", views_extra.SharedWithMeView.as_view(), name="shared-with-me-detail"),
    path("device-ingest/", views_extra.DeviceVitalsIngestView.as_view(), name="device-vitals-ingest"),
    path("sign/", views.SignDocumentView.as_view(), name="sign-document"),
    path("patients/<int:patient_id>/summary/", views.PatientClinicalSummaryView.as_view(), name="patient-clinical-summary"),
]
