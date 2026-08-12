from celery import shared_task
from django.utils import timezone

from .models import Task


@shared_task
def escalate_overdue_tasks():
    """Bumps priority on generic automation tasks past their due date so
    they surface above routine work (§6 escalation rules)."""
    overdue = Task.objects.filter(
        status__in=[Task.Status.PENDING, Task.Status.IN_PROGRESS],
        due_at__lt=timezone.now(),
    ).exclude(priority=Task.Priority.URGENT)

    updated = 0
    for task in overdue:
        next_priority = {
            Task.Priority.LOW: Task.Priority.NORMAL,
            Task.Priority.NORMAL: Task.Priority.HIGH,
            Task.Priority.HIGH: Task.Priority.URGENT,
        }.get(task.priority, Task.Priority.URGENT)
        task.priority = next_priority
        task.save(update_fields=["priority"])
        updated += 1
    return updated
