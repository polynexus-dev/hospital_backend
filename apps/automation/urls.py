from rest_framework.routers import DefaultRouter

from .views import EscalationRuleViewSet, TaskViewSet, WorkflowRunViewSet, WorkflowViewSet

router = DefaultRouter()
router.register("tasks", TaskViewSet, basename="task")
router.register("escalation-rules", EscalationRuleViewSet, basename="escalationrule")
router.register("workflows", WorkflowViewSet, basename="workflow")
router.register("workflow-runs", WorkflowRunViewSet, basename="workflowrun")

urlpatterns = router.urls

