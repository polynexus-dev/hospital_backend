from celery import shared_task


@shared_task
def send_payout_statements(payout_ids, channels, user_id=None):
    """Bulk 'send statements' for more than a handful of consultants."""
    from .payout_statements import send_many

    return send_many(payout_ids, channels, user_id)


@shared_task
def raise_recurring_invoices():
    """Daily: raise customer invoices from recurring templates that are due."""
    from .receivables import run_recurring

    return run_recurring()
