import logging

from celery import shared_task
from django.core.cache import cache

from . import service

logger = logging.getLogger(__name__)
LAST_STATE_KEY = "licensing:last_state"


@shared_task
def check_license():
    """Hourly (CELERY_BEAT_SCHEDULE): re-verify the installed licence from
    scratch, so expiry, a replaced file or a wound-back clock is noticed
    even when nobody is using the system. Records state changes."""
    if not service.is_on_premise():
        return "saas"
    cache.delete(service._cache_key())
    status = service.current_status()
    previous = cache.get(LAST_STATE_KEY)
    if previous != status.state:
        cache.set(LAST_STATE_KEY, status.state, None)
        logger.warning("Licence state changed: %s -> %s (%s)", previous, status.state, status.message)
        from apps.governance import services as gov
        from apps.governance.models import SecurityEvent

        gov.log_security_event(
            SecurityEvent.EventType.POLICY_CHANGED,
            details={"action": "license_state_changed", "from": previous, "to": status.state, "message": status.message},
        )
    return status.state
