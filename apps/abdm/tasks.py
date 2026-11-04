from celery import shared_task


@shared_task
def process_gateway_callback(callback_id):
    """One call from ABDM's gateway, answered asynchronously (apps.abdm.callbacks)."""
    from .callbacks import process

    process(callback_id)


@shared_task
def purge_expired_abdm_records():
    """Records received from other providers are deleted once their consent's dataEraseAt passes."""
    from .hiu import purge_expired

    return purge_expired()
