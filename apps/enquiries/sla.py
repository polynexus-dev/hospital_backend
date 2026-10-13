"""Enquiry response deadlines and escalation.

First contact
    A new enquiry must be contacted (moved out of New) within the hospital's
    enquiry escalation rule for its department (Automation > Escalation
    rules), or DEFAULT_FIRST_CONTACT_MINUTES. Breach, step 1: an urgent
    task for the owner. Still untouched one period later, step 2: a task for
    the rule's "escalate to" person (or a CRM manager), and the enquiry is
    reassigned to that person if the rule names one.

Follow-ups
    Once contacted, follow_up_date keeps the enquiry moving: when it passes
    with the enquiry still open, the owner gets one task for that date.

Tasks name the enquiry by number, not the person: task titles aren't
encrypted, enquiry names are.
"""
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from .models import Enquiry

DEFAULT_FIRST_CONTACT_MINUTES = 240
MIN_STEP_MINUTES = 30
MANAGER_TEMPLATES = ("crm_manager", "crm_super_admin", "hospital_administrator", "admin", "owner")
FOLLOW_UP_STAGES = (Enquiry.Stage.CONTACTED, Enquiry.Stage.SCHEDULED, Enquiry.Stage.FOLLOW_UP)


def _rule(enquiry):
    from apps.automation.models import EscalationRule

    rules = EscalationRule.objects.filter(hospital_id=enquiry.hospital_id, applies_to=EscalationRule.AppliesTo.ENQUIRY, is_active=True)
    if enquiry.department_id:
        specific = rules.filter(department_id=enquiry.department_id).order_by("escalate_after_minutes").first()
        if specific:
            return specific
    return rules.filter(department__isnull=True).order_by("escalate_after_minutes").first()


def first_contact_minutes(enquiry, rule=None):
    rule = rule or _rule(enquiry)
    return rule.escalate_after_minutes if rule else DEFAULT_FIRST_CONTACT_MINUTES


def first_contact_due(enquiry):
    return (enquiry.created_at or timezone.now()) + timedelta(minutes=first_contact_minutes(enquiry))


def stop_first_contact_sla(enquiry):
    """Called when the enquiry leaves New. Returns the extra fields to save."""
    if enquiry.first_contacted_at is None:
        enquiry.first_contacted_at = timezone.now()
    enquiry.sla_due_at = None
    enquiry.escalation_level = 0
    return ["first_contacted_at", "escalation_level"]


def _manager(enquiry, rule):
    from apps.accounts.models import User

    if rule and rule.escalate_to_id and rule.escalate_to.is_active:
        return rule.escalate_to
    managers = User.objects.filter(hospital_id=enquiry.hospital_id, is_active=True, role__template__in=MANAGER_TEMPLATES).exclude(pk=enquiry.assigned_to_id)
    for template in MANAGER_TEMPLATES:  # most specific role first
        m = managers.filter(role__template=template).order_by("id").first()
        if m:
            return m
    return None


def _task(enquiry, owner, title, description, priority):
    from apps.automation.models import Task

    return Task.objects.create(
        hospital_id=enquiry.hospital_id, title=title[:255], description=description, owner=owner, department_id=enquiry.department_id,
        priority=priority, due_at=timezone.now() + timedelta(hours=1),
        content_type=ContentType.objects.get_for_model(Enquiry), object_id=enquiry.pk,
    )


def _waited(enquiry, now):
    minutes = int((now - enquiry.created_at).total_seconds() // 60)
    return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"


def escalate_all(now=None):
    """Runs every 15 minutes (CELERY_BEAT_SCHEDULE). Returns tasks raised."""
    from apps.automation.models import Task

    from .services import reassign_enquiry

    now = now or timezone.now()
    raised = 0
    breached = Enquiry.objects.filter(stage=Enquiry.Stage.NEW, sla_due_at__lt=now, escalation_level__lt=2).select_related("assigned_to", "department")
    for e in breached:
        rule = _rule(e)
        label = f"enquiry #{e.pk} ({e.get_source_display()})"
        if e.escalation_level == 0:
            target = e.assigned_to or _manager(e, rule)
            _task(e, target, f"First contact overdue: {label}",
                  f"Waiting {_waited(e, now)} without contact; the deadline was {timezone.localtime(e.sla_due_at):%d %b %H:%M}. Call now and move it out of New.",
                  Task.Priority.URGENT)
        else:
            period = max(first_contact_minutes(e, rule), MIN_STEP_MINUTES)
            if e.last_escalated_at and now - e.last_escalated_at < timedelta(minutes=period):
                continue
            manager = _manager(e, rule)
            owner_name = e.assigned_to.get_full_name() or e.assigned_to.email if e.assigned_to_id else "nobody"
            _task(e, manager, f"Escalated: no contact on {label}",
                  f"Still not contacted after {_waited(e, now)} (owner: {owner_name}).", Task.Priority.URGENT)
            if rule and rule.escalate_to_id and manager and manager.pk != e.assigned_to_id:
                reassign_enquiry(e, manager, reason=f"No first contact within {first_contact_minutes(e, rule)} min (escalation rule '{rule.name}')")
        Enquiry.objects.filter(pk=e.pk).update(escalation_level=e.escalation_level + 1, last_escalated_at=now)
        raised += 1

    today = timezone.localdate(now)
    from django.db.models import F, Q

    overdue = (Enquiry.objects.filter(stage__in=FOLLOW_UP_STAGES, follow_up_date__lt=today)
               .filter(Q(follow_up_reminded_on__isnull=True) | Q(follow_up_reminded_on__lt=F("follow_up_date")))
               .select_related("assigned_to", "department"))
    for e in overdue:
        target = e.assigned_to or _manager(e, _rule(e))
        _task(e, target, f"Follow-up overdue: enquiry #{e.pk}", f"The follow-up was due on {e.follow_up_date:%d %b %Y}. Contact them and set the next date.",
              Task.Priority.HIGH)
        Enquiry.objects.filter(pk=e.pk).update(follow_up_reminded_on=today)
        raised += 1
    return raised
