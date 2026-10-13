"""Workflow engine: a trigger starts every active workflow for it, and its
steps run in order.

Event data carries ids (enquiry_id, patient_id, appointment_id ...) plus
event facts (from_stage, to_stage, score ...). Conditions read the *current*
record each time, so a step after a wait sees what's true now ("still New
after 24 hours?").

Steps
  condition   config {"field": "stage", "op": "eq", "value": "new"}
              ops: eq ne in not_in lt lte gt gte contains is_set not_set.
              Older {"condition": "score <= 6"} is still read. A condition the
              engine can't read stops the run (never silently passes).
  wait_delay  {"delay_minutes": 1440}: the run is parked and resumed by
              tasks.resume_waiting_workflows.
  create_task {"title", "assign_to": "owner"|"manager", "priority", "due_in_minutes"}
              linked to the enquiry / patient. Titles may use {enquiry_id},
              {source}, {stage}; never names (task titles aren't encrypted).
  send_whatsapp / send_sms  {"purpose": "<template purpose>", "fallback_sms": true}
              to the patient, or to the lead if not a patient yet.
  escalate    {"note"}: urgent task for the escalation manager.
"""
import logging
import re
from datetime import timedelta
from typing import Any, Dict, List

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from apps.automation.models import Task, Workflow, WorkflowRun, WorkflowStep

logger = logging.getLogger(__name__)

OPS = ("eq", "ne", "in", "not_in", "lt", "lte", "gt", "gte", "contains", "is_set", "not_set")
_LEGACY = re.compile(r"^\s*(\w+)\s*(<=|>=|==|!=|<|>)\s*'?\"?([\w.\- ]+?)'?\"?\s*$")
_LEGACY_OPS = {"<=": "lte", ">=": "gte", "==": "eq", "!=": "ne", "<": "lt", ">": "gt"}


# --- context ------------------------------------------------------------------


def current_facts(event: Dict[str, Any], hospital_id) -> Dict[str, Any]:
    """Event facts plus the live state of the record it's about."""
    facts = dict(event)
    enquiry = _enquiry(event, hospital_id)
    if enquiry is not None:
        facts.update({
            "stage": enquiry.stage, "source": enquiry.source, "urgency": enquiry.urgency, "score": enquiry.score,
            "department": enquiry.department.name if enquiry.department_id else "", "department_id": enquiry.department_id,
            "assigned": bool(enquiry.assigned_to_id), "assigned_to_id": enquiry.assigned_to_id, "escalation_level": enquiry.escalation_level,
            "service_requested": enquiry.service_requested, "is_patient": bool(enquiry.patient_id), "contacted": enquiry.first_contacted_at is not None,
        })
    return facts


def _enquiry(event, hospital_id):
    from apps.enquiries.models import Enquiry

    if not event.get("enquiry_id"):
        return None
    return Enquiry.objects.filter(pk=event["enquiry_id"], hospital_id=hospital_id).select_related("department", "patient", "assigned_to").first()


def _patient(event, hospital_id):
    from apps.patients.models import Patient

    if event.get("patient_id"):
        return Patient.objects.filter(pk=event["patient_id"], hospital_id=hospital_id).first()
    enquiry = _enquiry(event, hospital_id)
    return enquiry.patient if enquiry and enquiry.patient_id else None


# --- conditions -----------------------------------------------------------------


def parse_condition(config: Dict[str, Any]):
    if config.get("field") and config.get("op") in OPS:
        return config["field"], config["op"], config.get("value")
    m = _LEGACY.match(str(config.get("condition", "")))
    if m:
        return m.group(1), _LEGACY_OPS[m.group(2)], m.group(3)
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def check(facts, field, op, value) -> bool:
    actual = facts.get(field)
    if op == "is_set":
        return actual not in (None, "", [], False)
    if op == "not_set":
        return actual in (None, "", [], False)
    if op in ("in", "not_in"):
        options = value if isinstance(value, list) else [v.strip() for v in str(value).split(",")]
        hit = str(actual) in [str(v) for v in options]
        return hit if op == "in" else not hit
    if op == "contains":
        return str(value).lower() in str(actual or "").lower()
    if op in ("lt", "lte", "gt", "gte"):
        a, b = _num(actual), _num(value)
        if a is None or b is None:
            return False
        return {"lt": a < b, "lte": a <= b, "gt": a > b, "gte": a >= b}[op]
    same = str(actual).lower() == str(value).lower() if not isinstance(actual, bool) else actual == (str(value).lower() in ("true", "1", "yes"))
    return same if op == "eq" else not same


# --- actions ----------------------------------------------------------------------


def _link(event, hospital_id):
    enquiry = _enquiry(event, hospital_id)
    if enquiry is not None:
        return ContentType.objects.get_for_model(enquiry), enquiry.pk
    patient = _patient(event, hospital_id)
    if patient is not None:
        return ContentType.objects.get_for_model(patient), patient.pk
    return None, None


def _assignee(event, hospital_id, who):
    from apps.accounts.models import User

    enquiry = _enquiry(event, hospital_id)
    if who == "manager":
        if enquiry is not None:
            from apps.enquiries.sla import _manager, _rule

            return _manager(enquiry, _rule(enquiry))
        return User.objects.filter(hospital_id=hospital_id, is_active=True, role__template__in=("crm_manager", "hospital_administrator", "admin", "owner")).order_by("id").first()
    if str(who).isdigit():
        return User.objects.filter(pk=int(who), hospital_id=hospital_id, is_active=True).first()
    if enquiry is not None and enquiry.assigned_to_id:
        return enquiry.assigned_to
    owner_id = event.get("owner_id") or event.get("assigned_to_id")
    return User.objects.filter(pk=owner_id, hospital_id=hospital_id).first() if owner_id else None


def _title(template, facts):
    safe = {k: facts.get(k, "") for k in ("enquiry_id", "source", "stage", "patient_id", "appointment_id", "department")}
    try:
        return str(template).format(**safe)[:255]
    except (KeyError, IndexError, ValueError):
        return str(template)[:255]


def _create_task(wf, step, event, facts, hospital_id, *, priority=None, owner=None, title=None, description=None):
    ct, oid = _link(event, hospital_id)
    cfg = step.config or {}
    owner = owner or _assignee(event, hospital_id, cfg.get("assign_to", "owner"))
    task = Task.objects.create(
        hospital_id=hospital_id, title=_title(title or cfg.get("title") or f"{wf.name}: enquiry #{{enquiry_id}}", facts),
        description=description or cfg.get("description") or f"Created by workflow '{wf.name}'.",
        owner=owner, department_id=facts.get("department_id"), status=Task.Status.PENDING,
        priority=priority or cfg.get("priority") or Task.Priority.NORMAL,
        due_at=timezone.now() + timedelta(minutes=int(cfg.get("due_in_minutes") or 60)), content_type=ct, object_id=oid,
    )
    return task, owner


def _send(step, event, hospital_id):
    from apps.communications.models import Channel
    from apps.communications.services import send_message, send_to_enquiry

    cfg = step.config or {}
    purpose = cfg.get("purpose") or cfg.get("template")
    if not purpose:
        raise ValueError("No message template chosen for this step.")
    channel = Channel.WHATSAPP if step.action_type == WorkflowStep.ActionType.SEND_WHATSAPP else Channel.SMS
    fallback = Channel.SMS if channel == Channel.WHATSAPP and cfg.get("fallback_sms", True) else None
    enquiry = _enquiry(event, hospital_id)
    if enquiry is not None:
        msg = send_to_enquiry(enquiry=enquiry, channel=channel, purpose=purpose, fallback_channel=fallback)
    else:
        patient = _patient(event, hospital_id)
        if patient is None:
            return "skipped", "Nobody to message: the event has no patient or enquiry."
        from apps.clinical.notify import ensure_default_templates

        ensure_default_templates(patient.hospital)
        msg = send_message(patient=patient, channel=channel, purpose=purpose, context={"patient_name": patient.full_name, "hospital_name": patient.hospital.name},
                           fallback_channel=fallback)
    if msg is None:
        return "skipped", f"Not sent ('{purpose}'): opted out, lost lead, no number or no template."
    return ("passed" if msg.status != "failed" else "failed"), f"{msg.channel} '{purpose}' {msg.status}"


# --- running ----------------------------------------------------------------------


def run_steps(run: WorkflowRun, steps, event, hospital_id, start_order=0, dry_run=False):
    wf = run.workflow
    logs = list(run.log_output or [])
    run.status = WorkflowRun.Status.SUCCESS
    run.resume_at = run.next_step_order = None
    for step in [s for s in steps if s.order >= start_order]:
        entry = {"step_order": step.order, "step_type": step.step_type, "action_type": step.action_type,
                 "title": step.title or f"Step #{step.order}", "status": "passed", "details": "", "at": timezone.now().isoformat()}
        try:
            if step.step_type == WorkflowStep.StepType.TRIGGER:
                entry["details"] = f"Triggered by {run.trigger_event}"
            elif step.step_type == WorkflowStep.StepType.CONDITION:
                parsed = parse_condition(step.config or {})
                if parsed is None:
                    entry.update(status="failed", details=f"Condition not understood: {step.config}")
                    logs.append(entry)
                    run.status = WorkflowRun.Status.FAILED
                    break
                field, op, value = parsed
                facts = current_facts(event, hospital_id)
                if not check(facts, field, op, value):
                    entry.update(status="skipped", details=f"Stopped: {field} is {facts.get(field)!r}, needed {op} {value!r}")
                    logs.append(entry)
                    run.status = WorkflowRun.Status.STOPPED
                    break
                entry["details"] = f"{field} {op} {value!r}: yes ({facts.get(field)!r})"
            elif dry_run and step.step_type == WorkflowStep.StepType.ACTION:
                entry["details"] = f"Test run: would {step.get_action_type_display().lower()} {step.config or ''}".strip()
            elif step.action_type == WorkflowStep.ActionType.WAIT_DELAY:
                minutes = max(1, int((step.config or {}).get("delay_minutes") or 15))
                run.status = WorkflowRun.Status.WAITING
                run.resume_at = timezone.now() + timedelta(minutes=minutes)
                run.next_step_order = step.order + 1
                entry["details"] = f"Waiting {minutes} min, until {timezone.localtime(run.resume_at):%d %b %H:%M}"
                logs.append(entry)
                break
            elif step.action_type == WorkflowStep.ActionType.CREATE_TASK:
                task, owner = _create_task(wf, step, event, current_facts(event, hospital_id), hospital_id)
                entry["details"] = f"Task #{task.pk} for {owner.email if owner else 'the team (no owner)'}"
            elif step.action_type in (WorkflowStep.ActionType.SEND_WHATSAPP, WorkflowStep.ActionType.SEND_SMS):
                entry["status"], entry["details"] = _send(step, event, hospital_id)
            elif step.action_type == WorkflowStep.ActionType.ESCALATE:
                facts = current_facts(event, hospital_id)
                task, owner = _create_task(wf, step, event, facts, hospital_id, priority=Task.Priority.URGENT, owner=_assignee(event, hospital_id, "manager"),
                                           title=f"Escalated by '{wf.name}': enquiry #{{enquiry_id}}" if facts.get("enquiry_id") else f"Escalated by '{wf.name}'",
                                           description=(step.config or {}).get("note") or f"Escalated automatically by workflow '{wf.name}'.")
                entry["details"] = f"Urgent task #{task.pk} for {owner.email if owner else 'the team (no manager found)'}"
        except Exception as exc:  # one broken step must not take down the trigger that fired it
            logger.exception("Workflow %s step %s failed", wf.pk if wf else None, step.order)
            entry.update(status="failed", details=f"Error: {exc}")
            logs.append(entry)
            run.status = WorkflowRun.Status.FAILED
            break
        logs.append(entry)
    run.log_output = logs
    run.save()
    return run


def execute_workflow(trigger_type: str, event_data: Dict[str, Any], hospital_id: Any) -> List[WorkflowRun]:
    """Starts every active workflow of `hospital_id` for `trigger_type`."""
    runs = []
    for wf in Workflow.objects.filter(hospital_id=hospital_id, trigger_type=trigger_type, is_active=True).prefetch_related("steps"):
        run = WorkflowRun.objects.create(hospital_id=hospital_id, workflow=wf, trigger_event=trigger_type, status=WorkflowRun.Status.RUNNING,
                                         context=event_data, log_output=[])
        runs.append(run_steps(run, list(wf.steps.all()), event_data, hospital_id))
    return runs


def test_run(workflow, event_data, hospital_id) -> WorkflowRun:
    """Dry run for the builder's Test button: conditions are checked for real,
    actions and waits are only described."""
    run = WorkflowRun.objects.create(hospital_id=hospital_id, workflow=workflow, trigger_event=f"test:{workflow.trigger_type}",
                                     status=WorkflowRun.Status.RUNNING, context=event_data, log_output=[])
    return run_steps(run, list(workflow.steps.all()), event_data, hospital_id, dry_run=True)


def fire(trigger_type: str, event_data: Dict[str, Any], hospital_id: Any):
    """For signal handlers: run after the current transaction commits, and
    never let a workflow problem break the action that triggered it."""
    from django.db import transaction

    def go():
        try:
            execute_workflow(trigger_type, event_data, hospital_id)
        except Exception:
            logger.exception("Workflows for %s failed", trigger_type)

    transaction.on_commit(go)


def resume_due(now=None) -> int:
    now = now or timezone.now()
    resumed = 0
    for run in WorkflowRun.objects.filter(status=WorkflowRun.Status.WAITING, resume_at__lte=now).select_related("workflow"):
        if run.workflow is None or not run.workflow.is_active:
            run.status = WorkflowRun.Status.STOPPED
            run.log_output = list(run.log_output or []) + [{"status": "skipped", "details": "Workflow switched off while waiting.", "at": now.isoformat()}]
            run.resume_at = None
            run.save()
            continue
        run_steps(run, list(run.workflow.steps.all()), run.context or {}, run.hospital_id, start_order=run.next_step_order or 0)
        resumed += 1
    return resumed
