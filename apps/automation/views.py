from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.viewsets import TenantScopedViewSetMixin

from .models import EscalationRule, Task
from .serializers import EscalationRuleSerializer, TaskSerializer


class TaskViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = TaskSerializer
    queryset = Task.objects.all()
    filterset_fields = ["status", "priority", "owner", "department"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, created_by=self.request.user)

    @action(detail=True, methods=["post"])
    def claim(self, request, pk=None):
        task = self.get_object()
        task.owner = request.user
        task.status = Task.Status.IN_PROGRESS
        task.save(update_fields=["owner", "status"])
        return Response(TaskSerializer(task).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        task = self.get_object()
        task.status = Task.Status.DONE
        task.completed_at = timezone.now()
        task.save(update_fields=["status", "completed_at"])
        return Response(TaskSerializer(task).data)


class EscalationRuleViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = EscalationRuleSerializer
    queryset = EscalationRule.objects.all()
    filterset_fields = ["applies_to", "department", "is_active"]


class WorkflowViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """CRUD API for Visual Workflow Builder node chains."""

    from .models import Workflow
    from .serializers import WorkflowSerializer

    serializer_class = WorkflowSerializer
    queryset = Workflow.objects.all()
    filterset_fields = ["trigger_type", "is_active"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, created_by=self.request.user)

    @action(detail=True, methods=["post"])
    def test_run(self, request, pk=None):
        """Triggers a test execution run of the workflow."""
        from .engine import execute_workflow
        from .serializers import WorkflowRunSerializer

        workflow = self.get_object()
        sample_event = request.data.get("sample_event", {"score": 4, "source": "test_run"})
        runs = execute_workflow(workflow.trigger_type, sample_event, request.user.hospital_id)
        if runs:
            return Response(WorkflowRunSerializer(runs[0]).data)
        return Response({"detail": "Workflow executed but no runs created."}, status=200)


class WorkflowRunViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Read-only log viewset for workflow execution history."""

    from .models import WorkflowRun
    from .serializers import WorkflowRunSerializer

    serializer_class = WorkflowRunSerializer
    queryset = WorkflowRun.objects.all()
    filterset_fields = ["workflow", "trigger_event", "status"]

