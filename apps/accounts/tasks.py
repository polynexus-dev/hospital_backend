from celery import shared_task


@shared_task
def purge_sso_attempts():
    """Sign-in round-trip state older than a day has no further use."""
    from .sso import purge_old_attempts

    return purge_old_attempts()
