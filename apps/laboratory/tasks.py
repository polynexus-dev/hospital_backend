from celery import shared_task


@shared_task
def dispatch_lab_reports():
    """Sends verified lab reports that are due (LabSettings.auto_dispatch)."""
    from .lab_extras import dispatch_due

    return dispatch_due()
