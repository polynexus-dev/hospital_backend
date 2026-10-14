from celery import shared_task
from django.utils import timezone


@shared_task
def send_campaign_task(campaign_id):
    from .campaigns import send_campaign
    from .models import BroadcastCampaign

    campaign = BroadcastCampaign.objects.filter(pk=campaign_id, status=BroadcastCampaign.Status.SENDING).select_related("hospital", "template").first()
    if campaign is not None:
        send_campaign(campaign)


@shared_task
def dispatch_scheduled_campaigns():
    """Starts scheduled campaigns whose time has come."""
    from .campaigns import check_ready, start
    from .models import BroadcastCampaign

    started = 0
    for campaign in BroadcastCampaign.objects.filter(status=BroadcastCampaign.Status.SCHEDULED, scheduled_for__lte=timezone.now()).select_related("template"):
        problems = check_ready(campaign)
        if problems:
            BroadcastCampaign.objects.filter(pk=campaign.pk).update(status=BroadcastCampaign.Status.FAILED, error=problems[0][:255])
            continue
        start(campaign)
        started += 1
    return started
