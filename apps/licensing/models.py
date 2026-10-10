from django.db import models


class LicenseClock(models.Model):
    """Highest time this installation has ever seen (one row). If the
    system clock later reads earlier than this, someone wound it back to
    stretch an expired license."""

    last_seen_at = models.DateTimeField()

    @classmethod
    def advance(cls, now):
        """Records `now`; returns the previous high-water mark (or None)."""
        clock, created = cls.objects.get_or_create(pk=1, defaults={"last_seen_at": now})
        previous = None if created else clock.last_seen_at
        if previous is None or now > previous:
            cls.objects.filter(pk=1).update(last_seen_at=now)
        return previous
