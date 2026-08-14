from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from apps.core.models import Department, TenantScopedModel


class Task(TenantScopedModel):
    """Generic auto-created task with ownership (§6) — the backend
    foundation the future visual workflow builder's "action" step will
    write into. Anything that needs a human follow-up (recall a no-show,
    chase a stalled enquiry, action an escalation) creates one of these
    rather than each app inventing its own task list."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In progress"
        DONE = "done", "Done"
        CANCELLED = "cancelled", "Cancelled"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="automation_tasks")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="automation_tasks")

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    priority = models.CharField(max_length=16, choices=Priority.choices, default=Priority.NORMAL)
    due_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    source = GenericForeignKey("content_type", "object_id")

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["due_at"]
        indexes = [
            models.Index(fields=["hospital", "status", "due_at"]),
        ]

    def __str__(self):
        return self.title


class EscalationRule(TenantScopedModel):
    """Configuration surface for SLA-breach escalation (§6). Enquiry and
    callback SLA breach handling ship with sane P1 defaults baked into
    apps.enquiries.tasks / apps.telephony.tasks; a matching EscalationRule
    row here lets ops override the threshold and who it escalates to
    without a code change."""

    class AppliesTo(models.TextChoices):
        ENQUIRY = "enquiry", "Enquiry"
        CALLBACK_TASK = "callback_task", "Callback task"
        AUTOMATION_TASK = "automation_task", "Automation task"

    name = models.CharField(max_length=150)
    applies_to = models.CharField(max_length=32, choices=AppliesTo.choices)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="escalation_rules")
    escalate_after_minutes = models.PositiveIntegerField()
    escalate_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Workflow(TenantScopedModel):
    """Phase 2 Visual Workflow Builder model — defines an automated event-driven rule chain."""

    class TriggerType(models.TextChoices):
        MISSED_CALL = "missed_call", "Missed Call / Unanswered Call"
        ENQUIRY_STAGE_CHANGED = "enquiry_stage_changed", "Enquiry Stage Changed"
        APPOINTMENT_NO_SHOW = "appointment_no_show", "Appointment No-Show"
        NPS_DETRACTOR = "nps_detractor", "NPS Detractor Rating Received"
        PATIENT_RECALL_DUE = "patient_recall_due", "Patient Recall Due (scheduled)"

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    trigger_type = models.CharField(max_length=48, choices=TriggerType.choices, default=TriggerType.MISSED_CALL)
    is_active = models.BooleanField(default=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name


class WorkflowStep(models.Model):
    """An individual node step within a Workflow node chain (Trigger ➔ Condition ➔ Action)."""

    class StepType(models.TextChoices):
        TRIGGER = "trigger", "Trigger Node"
        CONDITION = "condition", "Condition Filter Node"
        ACTION = "action", "Action Node"

    class ActionType(models.TextChoices):
        CREATE_TASK = "create_task", "Create Task / Callback Task"
        SEND_WHATSAPP = "send_whatsapp", "Send WhatsApp Notification"
        SEND_SMS = "send_sms", "Send SMS Notification"
        ESCALATE = "escalate", "Escalate to Manager / Owner"
        WAIT_DELAY = "wait_delay", "Wait / Delay Timer"

    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="steps")
    order = models.PositiveIntegerField(default=1)
    step_type = models.CharField(max_length=24, choices=StepType.choices, default=StepType.ACTION)
    action_type = models.CharField(max_length=32, choices=ActionType.choices, default=ActionType.CREATE_TASK)
    title = models.CharField(max_length=150, blank=True)

    # Config options e.g. {"condition": "score <= 6"}, {"action": "send_whatsapp", "template": "reminder"}
    config = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.workflow.name} - Step #{self.order}: {self.title or self.step_type}"


class WorkflowRun(TenantScopedModel):
    """Audit log history trace for workflow executions."""

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        RUNNING = "running", "Running"

    workflow = models.ForeignKey(Workflow, on_delete=models.SET_NULL, null=True, blank=True, related_name="runs")
    trigger_event = models.CharField(max_length=100)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SUCCESS)

    context = models.JSONField(default=dict, blank=True)
    log_output = models.JSONField(default=list, blank=True)
    executed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-executed_at"]

    def __str__(self):
        return f"Run #{self.id} - {self.workflow_id} ({self.status})"

