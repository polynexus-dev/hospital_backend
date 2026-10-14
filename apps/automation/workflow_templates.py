"""Ready-made workflows a hospital can install with one click (Workflows >
Templates) and then adjust. Installed switched off, so nothing starts
messaging patients until someone has looked at it and turned it on."""

T, C, A = "trigger", "condition", "action"

WORKFLOW_TEMPLATES = [
    {
        "key": "new_lead_no_contact_24h",
        "name": "New lead not contacted in 24 hours",
        "description": "A day after a new enquiry, if nobody has contacted it yet: send the lead a WhatsApp follow-up and give the owner a high-priority task.",
        "trigger_type": "enquiry_created",
        "steps": [
            (T, "create_task", "New enquiry", {}),
            (A, "wait_delay", "Wait 24 hours", {"delay_minutes": 1440}),
            (C, "create_task", "Still not contacted?", {"field": "stage", "op": "eq", "value": "new"}),
            (A, "send_whatsapp", "WhatsApp follow-up to the lead", {"purpose": "enquiry_follow_up", "fallback_sms": True}),
            (A, "create_task", "Task: call the lead today", {"title": "Call today: enquiry #{enquiry_id} not contacted in 24 h", "assign_to": "owner", "priority": "high", "due_in_minutes": 240}),
        ],
    },
    {
        "key": "contacted_lead_quiet_2d",
        "name": "Contacted lead gone quiet for 2 days",
        "description": "Two days after first contact, if the enquiry hasn't moved on (still Contacted): nudge the lead on WhatsApp and remind the owner.",
        "trigger_type": "enquiry_stage_changed",
        "steps": [
            (T, "create_task", "Enquiry stage changed", {}),
            (C, "create_task", "Moved to Contacted?", {"field": "to_stage", "op": "eq", "value": "contacted"}),
            (A, "wait_delay", "Wait 2 days", {"delay_minutes": 2880}),
            (C, "create_task", "Still Contacted?", {"field": "stage", "op": "eq", "value": "contacted"}),
            (A, "send_whatsapp", "WhatsApp nudge", {"purpose": "enquiry_follow_up", "fallback_sms": True}),
            (A, "create_task", "Task: follow up", {"title": "Follow up enquiry #{enquiry_id}: no progress in 2 days", "assign_to": "owner", "priority": "normal", "due_in_minutes": 480}),
        ],
    },
    {
        "key": "lead_went_quiet_sequence",
        "name": "Lead went quiet: 3-step follow-up",
        "description": "When an open lead hasn't replied or moved for 2 days: WhatsApp (SMS if WhatsApp fails) at once, an email 3 days later, "
                       "and after 9 quiet days a task for the owner to call or close it. Stops as soon as the lead replies, a call is answered "
                       "or the enquiry moves on.",
        "trigger_type": "enquiry_went_quiet",
        "steps": [
            (T, "create_task", "Lead went quiet", {}),
            (A, "send_whatsapp", "WhatsApp follow-up", {"purpose": "enquiry_follow_up", "fallback_sms": True}),
            (A, "wait_delay", "Wait 3 days", {"delay_minutes": 4320}),
            (C, "create_task", "Still quiet (5+ days)?", {"field": "quiet_days", "op": "gte", "value": 5}),
            (C, "create_task", "Still open?", {"field": "stage", "op": "in", "value": "new,contacted,scheduled,follow_up"}),
            (A, "send_email", "Email follow-up", {"purpose": "enquiry_follow_up_email"}),
            (A, "wait_delay", "Wait 4 days", {"delay_minutes": 5760}),
            (C, "create_task", "Still quiet (9+ days)?", {"field": "quiet_days", "op": "gte", "value": 9}),
            (C, "create_task", "Still open?", {"field": "stage", "op": "in", "value": "new,contacted,scheduled,follow_up"}),
            (A, "create_task", "Task: call or close", {"title": "Enquiry #{enquiry_id} quiet for 9 days: call once more or mark lost", "assign_to": "owner",
                                                        "priority": "normal", "due_in_minutes": 1440}),
        ],
    },
    {
        "key": "hot_lead_fast_track",
        "name": "Urgent or high-score lead: fast track",
        "description": "When an urgent or high-scoring enquiry arrives, give the owner an urgent task due in 15 minutes.",
        "trigger_type": "enquiry_created",
        "steps": [
            (T, "create_task", "New enquiry", {}),
            (C, "create_task", "Score 70 or more?", {"field": "score", "op": "gte", "value": 70}),
            (A, "create_task", "Urgent task: call now", {"title": "Hot lead: call enquiry #{enquiry_id} now", "assign_to": "owner", "priority": "urgent", "due_in_minutes": 15}),
        ],
    },
    {
        "key": "missed_call_callback",
        "name": "Missed call: call back within 15 minutes",
        "description": "Every missed call gets an urgent callback task.",
        "trigger_type": "missed_call",
        "steps": [
            (T, "create_task", "Missed call", {}),
            (A, "create_task", "Callback task", {"title": "Call back a missed caller", "assign_to": "owner", "priority": "urgent", "due_in_minutes": 15}),
        ],
    },
    {
        "key": "no_show_recall",
        "name": "No-show: invite to rebook",
        "description": "When a patient misses an appointment, send a WhatsApp invitation to rebook.",
        "trigger_type": "appointment_no_show",
        "steps": [
            (T, "create_task", "Appointment no-show", {}),
            (A, "send_whatsapp", "WhatsApp: book a new time", {"purpose": "no_show_recall", "fallback_sms": True}),
        ],
    },
    {
        "key": "detractor_escalation",
        "name": "Unhappy patient (NPS 0–6): tell a manager",
        "description": "A detractor score raises an urgent task for the escalation manager, on top of the service-recovery case.",
        "trigger_type": "nps_detractor",
        "steps": [
            (T, "create_task", "Detractor feedback", {}),
            (A, "escalate", "Escalate to manager", {"note": "Patient gave a detractor score. Call them within 24 hours."}),
        ],
    },
]


def install(template_key, hospital, user):
    from .models import Workflow, WorkflowStep

    tpl = next((t for t in WORKFLOW_TEMPLATES if t["key"] == template_key), None)
    if tpl is None:
        return None
    wf = Workflow.objects.create(hospital=hospital, name=tpl["name"], description=tpl["description"], trigger_type=tpl["trigger_type"],
                                 is_active=False, created_by=user)
    for order, (step_type, action_type, title, config) in enumerate(tpl["steps"], start=1):
        WorkflowStep.objects.create(workflow=wf, order=order, step_type=step_type, action_type=action_type, title=title, config=config)
    return wf
