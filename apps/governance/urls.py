from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    AccreditationViewSet,
    AuditRollbackView,
    AuditRuleViewSet,
    BackupViewSet,
    HelpArticleViewSet,
    PublicAccreditationsView,
    ReleaseNoteViewSet,
    RetentionPolicyView,
    SecurityEventViewSet,
    SecurityPolicyView,
)

router = DefaultRouter()
router.register("security-events", SecurityEventViewSet, basename="securityevent")
router.register("audit-rules", AuditRuleViewSet, basename="auditrule")
router.register("backups", BackupViewSet, basename="backup")
router.register("help", HelpArticleViewSet, basename="helparticle")
router.register("accreditations", AccreditationViewSet, basename="accreditation")
router.register("release-notes", ReleaseNoteViewSet, basename="releasenote")

urlpatterns = router.urls + [
    path("security-policy/", SecurityPolicyView.as_view(), name="security-policy"),
    path("retention-policy/", RetentionPolicyView.as_view(), name="retention-policy"),
    path("audit-logs/<int:pk>/rollback/", AuditRollbackView.as_view(), name="audit-rollback"),
    path("public/accreditations/", PublicAccreditationsView.as_view(), name="public-accreditations"),
]
