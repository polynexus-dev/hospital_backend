from celery import shared_task


@shared_task
def poll_tpa_mailboxes():
    """Imports new insurer / TPA emails into the insurance inbox (apps.tpa.mailbox)."""
    from .mailbox import poll_all

    return poll_all()
