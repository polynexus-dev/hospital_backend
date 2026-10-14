"""Bulk WhatsApp / SMS / email campaigns that actually send.

A campaign picks an audience, then each recipient gets one real message
through the channel's provider (apps.communications.adapters), logged as a
Message linked to the campaign. The counts on the campaign are those
messages: sent / failed when the provider answers, delivered / read when it
reports back (apps.communications.delivery) — nothing is estimated.

Marketing rules apply to every recipient: marketing opt-outs, STOP replies
and email unsubscribes are skipped, and every email carries an unsubscribe
link. SMS needs a DLT-registered template (MSG91 template id), so an SMS
campaign must use a Template; WhatsApp to people who haven't written in the
last 24 hours needs a Meta-approved template too.
"""
import logging
from datetime import date, timedelta

from django.db.models import Q
from django.utils import timezone

from apps.patients.models import Patient

from .models import BroadcastCampaign, Channel, ConsentOptOut, Message

logger = logging.getLogger(__name__)

A = BroadcastCampaign.TargetAudience
OPEN_LEAD_STAGES = ["new", "contacted", "scheduled", "follow_up"]


def audience(campaign_or_hospital, target):
    """(patients queryset, enquiries queryset) for a target audience."""
    from apps.enquiries.models import Enquiry

    hospital = getattr(campaign_or_hospital, "hospital", campaign_or_hospital)
    patients = Patient.objects.filter(hospital=hospital, is_active=True)
    no_enquiries = Enquiry.objects.none()
    if target == A.ALL_PATIENTS:
        return patients, no_enquiries
    if target == A.SENIOR_CITIZENS:
        return patients.filter(birth_year__lte=date.today().year - 60), no_enquiries
    if target == A.CHRONIC_CARE:
        return patients.filter(next_recall_due_at__isnull=False, next_recall_due_at__lte=timezone.now() + timedelta(days=30)), no_enquiries
    leads = Enquiry.objects.filter(hospital=hospital, duplicate_of__isnull=True, patient__isnull=True)
    if target == A.UNCONVERTED_LEADS:
        return Patient.objects.none(), leads.filter(stage__in=OPEN_LEAD_STAGES)
    if target == A.FOLLOW_UP_LEADS:
        return Patient.objects.none(), leads.filter(Q(stage="follow_up") | Q(follow_up_date__isnull=False)).exclude(stage__in=["lost", "completed"])
    return Patient.objects.none(), no_enquiries


def audience_counts(hospital):
    out = {}
    for target in A.values:
        patients, leads = audience(hospital, target)
        out[target] = patients.count() + leads.count()
    return out


def check_ready(campaign):
    """Problems that would make every message fail; empty list = OK."""
    problems = []
    if campaign.channel not in (Channel.WHATSAPP, Channel.SMS, Channel.EMAIL):
        problems.append("Campaigns go out by WhatsApp, SMS or email.")
    if campaign.template_id is None and not campaign.custom_message.strip():
        problems.append("Write a message or choose a template.")
    if campaign.channel == Channel.SMS and not (campaign.template_id and campaign.template.provider_template_id):
        problems.append("SMS campaigns need a template with its MSG91 / DLT template id.")
    if campaign.template_id and campaign.template.channel != campaign.channel:
        problems.append("The template is for a different channel.")
    return problems


def _content(campaign, context):
    """(body, subject, template, error) for one recipient."""
    from .services import render

    template = campaign.template
    if template is not None:
        body, subject, error = render(template, context)
        return body, subject or campaign.title, template, error
    body = campaign.custom_message
    for key, value in context.items():
        body = body.replace("{{" + key + "}}", str(value)).replace("{" + key + "}", str(value))
    return body, campaign.title, None, ""


def _opted_out(campaign, channel, address, patient=None):
    from .services import is_suppressed

    if patient is not None and ConsentOptOut.objects.filter(
            patient=patient, channel=channel, is_opted_out=True, purpose__in=[ConsentOptOut.Purpose.MARKETING, ConsentOptOut.Purpose.ALL]).exists():
        return True
    return is_suppressed(campaign.hospital_id, channel, address)


def send_campaign(campaign):
    """Sends to everyone in the audience who hasn't had this campaign yet,
    so a crash part-way can be resumed, and a failed campaign retried, by
    running it again. Earlier failed attempts stay in the inbox as history
    but no longer count towards the campaign."""
    from .services import deliver

    hospital = campaign.hospital
    channel = campaign.channel
    patients, leads = audience(campaign, campaign.target_audience)
    Message.objects.filter(campaign=campaign, status=Message.Status.FAILED).update(campaign=None)
    already = Message.objects.filter(campaign=campaign)
    done_patients = set(already.exclude(patient__isnull=True).values_list("patient_id", flat=True))
    done_leads = set(already.exclude(enquiry__isnull=True).values_list("enquiry_id", flat=True))
    skipped = 0
    recipients = [("patient", p) for p in patients.iterator() if p.pk not in done_patients]
    recipients += [("lead", e) for e in leads.iterator() if e.pk not in done_leads]
    total = len(done_patients) + len(done_leads) + len(recipients)
    BroadcastCampaign.objects.filter(pk=campaign.pk).update(total_recipients=total)

    for kind, person in recipients:
        patient = person if kind == "patient" else None
        address = (person.email if channel == Channel.EMAIL else person.mobile) or ""
        name = person.full_name if patient else person.name
        if not address or _opted_out(campaign, channel, address, patient):
            skipped += 1
            continue
        context = {"patient_name": name or "", "hospital_name": hospital.name}
        body, subject, template, error = _content(campaign, context)
        deliver(hospital=hospital, channel=channel, to=address, body=body, subject=subject, template=template, context=context,
                patient=patient, enquiry=None if patient else person, sent_by=campaign.created_by, campaign=campaign, marketing=True, error=error)

    from .delivery import refresh_campaign_counts

    refresh_campaign_counts(campaign.pk)
    campaign.refresh_from_db()
    campaign.skipped_count = skipped
    campaign.status = BroadcastCampaign.Status.COMPLETED if campaign.sent_count or not campaign.failed_count else BroadcastCampaign.Status.FAILED
    if campaign.status == BroadcastCampaign.Status.FAILED:
        first = Message.objects.filter(campaign=campaign, status=Message.Status.FAILED).exclude(error="").first()
        campaign.error = (first.error if first else "Every message failed.")[:255]
    campaign.completed_at = timezone.now()
    campaign.save(update_fields=["skipped_count", "status", "error", "completed_at", "updated_at"])
    return campaign


def start(campaign):
    """Marks the campaign sending and hands it to the worker."""
    from django.db import transaction

    from .tasks import send_campaign_task

    BroadcastCampaign.objects.filter(pk=campaign.pk).update(status=BroadcastCampaign.Status.SENDING, started_at=timezone.now(), error="")
    campaign.refresh_from_db()

    def dispatch():
        try:
            send_campaign_task.delay(campaign.pk)
        except Exception:
            logger.exception("Couldn't queue campaign %s", campaign.pk)
            BroadcastCampaign.objects.filter(pk=campaign.pk).update(status=BroadcastCampaign.Status.FAILED, error="Couldn't reach the task queue.")

    transaction.on_commit(dispatch)
    return campaign
