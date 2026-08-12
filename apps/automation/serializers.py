from rest_framework import serializers
from .models import EscalationRule, Task, Workflow, WorkflowRun, WorkflowStep




class TaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = [
            "id", "title", "description", "owner", "department",
            "status", "priority", "due_at", "completed_at",
            "content_type", "object_id", "created_by", "created_at",
        ]
        read_only_fields = ["id", "content_type", "object_id", "created_by", "created_at"]


class EscalationRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = EscalationRule
        fields = ["id", "name", "applies_to", "department", "escalate_after_minutes", "escalate_to", "is_active"]
        read_only_fields = ["id"]


class WorkflowStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowStep
        fields = ["id", "order", "step_type", "action_type", "title", "config"]
        read_only_fields = ["id"]


class WorkflowSerializer(serializers.ModelSerializer):
    steps = WorkflowStepSerializer(many=True, required=False)

    class Meta:
        model = Workflow
        fields = [
            "id", "name", "description", "trigger_type", "is_active",
            "steps", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def create(self, validated_data):
        steps_data = validated_data.pop("steps", [])
        workflow = Workflow.objects.create(**validated_data)
        for order, step_d in enumerate(steps_data, start=1):
            WorkflowStep.objects.create(workflow=workflow, order=step_d.get("order", order), **step_d)
        return workflow

    def update(self, instance, validated_data):
        steps_data = validated_data.pop("steps", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if steps_data is not None:
            instance.steps.all().delete()
            for order, step_d in enumerate(steps_data, start=1):
                WorkflowStep.objects.create(workflow=instance, order=step_d.get("order", order), **step_d)
        return instance


class WorkflowRunSerializer(serializers.ModelSerializer):
    workflow_name = serializers.CharField(source="workflow.name", read_only=True)

    class Meta:
        model = WorkflowRun
        fields = [
            "id", "workflow", "workflow_name", "trigger_event",
            "status", "context", "log_output", "executed_at",
        ]
        read_only_fields = ["id", "executed_at"]

