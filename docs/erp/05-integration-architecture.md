# CRM ↔ ERP Integration Architecture

## Why events, not shared tables or REST calls to itself

Both domains live in one Django process. The naive "keep them separate" implementations are either bad:

- **Shared tables / cross-app FKs everywhere** — exactly the "uncontrolled database coupling" your spec's Part 3 forbids. A CRM app directly reading `ipd.Admission` couples its behavior to ERP schema changes with no seam to control the blast radius.
- **REST calls to your own process** — CRM code calling `requests.post("http://localhost:8000/api/v1/opd/...")` to talk to ERP code in the same Django process adds network-call latency, failure modes, and auth complexity for zero isolation benefit, since a crash in one still crashes the other (same process).

The right middle ground, and what's built here: **Django's built-in signal dispatcher as a domain event bus.** ERP apps emit named events on state transitions; CRM apps (and other ERP apps) subscribe without either side importing the other's models directly.

## Discovery during Phase 3: this pattern already existed

Before Phase 3 wrote a line of `opd` code, `apps/appointments/signals.py` already had `appointment_completed` and `appointment_no_show` — `apps.feedback` and `apps.automation` already subscribe to them (`apps/feedback/signals.py`, `apps/automation/signals.py`), each connected via `AppConfig.ready()` importing its own `signals.py`. That's exactly the pattern this document specifies below, independently arrived at before this plan existed. Phase 3 didn't invent a new event system — it added one more signal (`appointment_checked_in`) to the same existing module, and `apps/opd/signals.py` subscribes to it the same way `feedback`/`automation` already subscribe to the other two. The "shipped pattern" in this section is now validated against real, pre-existing code, not just designed on paper.

## Event catalogue (Phase-tagged — grows as each phase ships)

| Event | Emitted by | Phase | Consumed by | Effect |
|---|---|---|---|---|
| `appointment_completed` | `appointments.Appointment` (pre-existing) | 1 | `feedback`; **Phase 7 `billing` subscribes to the same signal** for OPD bill drafting — no separate `opd.encounter_completed` event needed, since `Encounter` (below) has no status of its own to complete; the visit's completion is still, correctly, an `Appointment` concern | Post-visit NPS/feedback request; OPD bill draft |
| `appointment_no_show` | `appointments.Appointment` (pre-existing) | 1 | `automation` | Auto-recall task |
| `appointment_checked_in` | `appointments.Appointment` | 3 (shipped) | `opd` | Creates `opd.Encounter` via `get_or_create` (idempotent — see `apps/opd/signals.py`) |
| `patient.registered` | `patients.Patient` (first UHID assignment) | 2 | `automation` | Welcome/registration-confirmation task |
| *(admission decision — resolved, not an event)* | **Decided in Phase 4**: no signal at all. `Admission` is created directly via `POST /ipd/admissions/` with an optional `source_encounter` FK back to the `opd.Encounter` that led to it (nullable — plenty of admissions have no preceding OPD visit: planned surgery, the emergency path Phase 6 adds). Forcing a new `Appointment.Status` value in just to hang a signal off would have coupled OPD's already-tested state machine to IPD for no real benefit — a plain back-reference is simpler and equally traceable. | 4 (shipped) | — | — |
| `laboratory.result_critical` | `laboratory.LabResult` (**shipped, Phase 5** — fired from `LabResultViewSet.perform_create`/`perform_update`, not the model's own `save()`, matching how every other event in this table is emitted from the service/view layer rather than a model signal) | 5 (shipped) | `automation` (**shipped** — creates an urgent, +1h-due `Task`, same shape as `appointment_no_show`'s recall task); `nursing` **not yet subscribed** — no nursing-facing alert inbox exists yet to receive it, deferred rather than built speculatively | Urgent alert task to attending doctor |
| `ipd.patient_discharged` | `ipd.Admission` (**signal shipped, Phase 4** — fired from `apps.ipd.services.discharge_patient`; no receivers yet, `billing`/`feedback`/`automation` subscribe in their own later phases, same as `appointment_completed` was defined in Phase 1 and only got its first `feedback` receiver at the same time) | 4/6/7 | `billing`, `feedback`, `automation`, `analytics` | Final bill trigger, NPS survey dispatch, post-discharge recall task, MIS update |
| `emergency.admission_required` | `emergency.EDVisit` | 6 | `ipd` | Creates `ipd.Admission` with `admission_type=emergency`, links `source_ed_visit` |
| `billing.bill_finalized` | `billing.Bill` | 7 | `finance`, `tpa` | Ledger entry, claim submission trigger for insured patients |
| `hr.employee_onboarded` | `hr.Employee` | 8 | `accounts` | Prompts creation of the linked `accounts.User` + `Role` assignment |

## Implementation pattern

```python
# apps/ipd/signals.py
from django.dispatch import Signal

patient_discharged = Signal()  # sender=Admission instance

# apps/ipd/models.py, inside Admission.save() or a dedicated discharge() method
from .signals import patient_discharged
...
def discharge(self, ...):
    self.status = self.Status.DISCHARGED
    self.discharged_at = timezone.now()
    self.save(update_fields=["status", "discharged_at"])
    patient_discharged.send(sender=self.__class__, admission=self)
```

```python
# apps/feedback/apps.py — connect in AppConfig.ready(), matching Django's
# standard signal-registration pattern (already used for the existing
# no-show -> recall-task hook in apps.automation)
from apps.ipd.signals import patient_discharged

def on_patient_discharged(sender, admission, **kwargs):
    from .services import schedule_post_discharge_nps
    schedule_post_discharge_nps(admission.patient)

patient_discharged.connect(on_patient_discharged)
```

`feedback` imports `ipd.signals` (a tiny, stable module — just `Signal()` declarations, no models) — never `ipd.models`. That's the actual boundary: **apps may import each other's `signals.py`, never each other's `models.py` or `views.py`** across the CRM/ERP line. Within one domain (e.g. `ipd` importing `facilities.Bed`), normal FK imports are fine — the discipline is specifically at the CRM/ERP seam, matching what your spec actually asks for (controlled integration, not zero coupling anywhere).

## External HIS path (kept, see `00-overview.md` §4)

For a hospital using an external HIS instead of the modules built here, `apps/integrations` continues to be the analogous boundary — its adapter (`connectors.py`) becomes the thing that *would* emit these same named events (or their nearest equivalent) after a poll/webhook from the real HIS, so `feedback`/`automation`/`billing` subscribers don't need to know or care whether `patient_discharged` originated from `apps.ipd` or from a webhook off a third-party HIS. This is the concrete reason the event-based design matters even though everything lives in one codebase today: the event contract is the actual integration surface, and it's the same contract either way.

## What this deliberately does NOT do

- No message queue / async event bus (Celery task dispatch from a signal handler is fine and already the pattern for slow work — e.g. an actual WhatsApp send should be a Celery task kicked off from the signal handler, not synchronous in the request). Django signals are synchronous and in-process; that's correct for "create a follow-up Task row," wrong for "call the WhatsApp API," and the existing `apps.communications`/`apps.automation` Celery task patterns already show how to split that.
- No event replay/audit log of the events themselves — the underlying model changes are already covered by `AuditLog` (`07-audit-and-security.md`); a duplicate event-log would be redundant state to keep consistent.
- No cross-hospital event fan-out — every event carries a tenant-scoped model instance; a signal handler that forgets to check `hospital_id` before acting is a bug, not a framework guarantee. Handlers must scope explicitly, same discipline as every existing tenant-scoped query.
