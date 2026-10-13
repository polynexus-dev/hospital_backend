from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.settings import api_settings

from apps.core.viewsets import TenantScopedViewSetMixin

from .models import EscalationRule, Task
from .serializers import EscalationRuleSerializer, TaskSerializer


class TaskViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
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


# Workflows message patients and escalation rules reassign leads: both need the
# role's automation permissions (the project default), not just a login.
class EscalationRuleViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = api_settings.DEFAULT_PERMISSION_CLASSES
    serializer_class = EscalationRuleSerializer
    queryset = EscalationRule.objects.all()
    filterset_fields = ["applies_to", "department", "is_active"]


class WorkflowViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """CRUD API for Visual Workflow Builder node chains."""

    from .models import Workflow
    from .serializers import WorkflowSerializer

    permission_classes = api_settings.DEFAULT_PERMISSION_CLASSES
    serializer_class = WorkflowSerializer
    queryset = Workflow.objects.all()
    filterset_fields = ["trigger_type", "is_active"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, created_by=self.request.user)

    @action(detail=True, methods=["post"])
    def test_run(self, request, pk=None):
        """Dry run of this workflow only: conditions are checked against the
        sample event (or the enquiry it names), actions are only described."""
        from .engine import test_run
        from .serializers import WorkflowRunSerializer

        if not request.user.has_perm("automation.change_workflow"):
            return Response({"detail": "You can't test workflows."}, status=403)
        workflow = self.get_object()
        sample_event = request.data.get("sample_event") or {"score": 4, "source": "test_run"}
        return Response(WorkflowRunSerializer(test_run(workflow, sample_event, request.user.hospital_id)).data)

    @action(detail=False, methods=["get"])
    def templates(self, request):
        from .workflow_templates import WORKFLOW_TEMPLATES

        installed = set(self.get_queryset().values_list("name", flat=True))
        return Response([{"key": t["key"], "name": t["name"], "description": t["description"], "trigger_type": t["trigger_type"],
                          "steps": [title for _, _, title, _ in t["steps"]], "installed": t["name"] in installed} for t in WORKFLOW_TEMPLATES])

    @action(detail=False, methods=["post"], url_path="install-template")
    def install_template(self, request):
        from .serializers import WorkflowSerializer
        from .workflow_templates import install

        if not request.user.has_perm("automation.add_workflow"):
            return Response({"detail": "You can't create workflows."}, status=403)
        wf = install(str(request.data.get("key", "")), request.user.hospital, request.user)
        if wf is None:
            return Response({"key": "Unknown template."}, status=400)
        return Response(WorkflowSerializer(wf).data, status=201)


class WorkflowRunViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Read-only log viewset for workflow execution history."""

    from .models import WorkflowRun
    from .serializers import WorkflowRunSerializer

    permission_classes = api_settings.DEFAULT_PERMISSION_CLASSES
    serializer_class = WorkflowRunSerializer
    # select_related: WorkflowRunSerializer.workflow_name (source="workflow.name")
    queryset = WorkflowRun.objects.select_related("workflow")
    filterset_fields = ["workflow", "trigger_event", "status"]

