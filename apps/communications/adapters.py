"""
Pluggable send-side adapters, one per channel. The doc names concrete
providers per channel (WhatsApp via AWS End User Messaging, SMS via a
DLT-registered route, email via SES/Brevo) but credentials for this
hospital aren't provisioned yet, so every channel defaults to a stub that
logs instead of sending. Wiring a real provider later means adding one
adapter class here and setting the corresponding settings.*_PROVIDER value
— no changes anywhere else in the codebase.
"""
import logging
from abc import ABC, abstractmethod

from django.conf import settings

logger = logging.getLogger("communications.adapters")


class MessageProvider(ABC):
    @abstractmethod
    def send(self, *, to: str, body: str, subject: str = "") -> str:
        """Sends the message, returns the provider's message id."""


class StubWhatsAppProvider(MessageProvider):
    """Stands in for AWS End User Messaging (Part A §15 — already live per
    the hospital's own infra, credentials pending for this integration)."""

    def send(self, *, to: str, body: str, subject: str = "") -> str:
        logger.info("STUB WhatsApp -> %s: %s", to, body)
        return f"stub-whatsapp-{to}"


class StubSMSProvider(MessageProvider):
    """Stands in for a DLT-registered SMS route."""

    def send(self, *, to: str, body: str, subject: str = "") -> str:
        logger.info("STUB SMS -> %s: %s", to, body)
        return f"stub-sms-{to}"


class StubEmailProvider(MessageProvider):
    """Stands in for SES / Brevo."""

    def send(self, *, to: str, body: str, subject: str = "") -> str:
        logger.info("STUB Email -> %s [%s]: %s", to, subject, body)
        return f"stub-email-{to}"


_WHATSAPP_PROVIDERS = {"stub": StubWhatsAppProvider}
_SMS_PROVIDERS = {"stub": StubSMSProvider}
_EMAIL_PROVIDERS = {"stub": StubEmailProvider}


def get_whatsapp_provider() -> MessageProvider:
    return _WHATSAPP_PROVIDERS.get(settings.WHATSAPP_PROVIDER, StubWhatsAppProvider)()


def get_sms_provider() -> MessageProvider:
    return _SMS_PROVIDERS.get(settings.SMS_PROVIDER, StubSMSProvider)()


def get_email_provider() -> MessageProvider:
    return _EMAIL_PROVIDERS.get(settings.EMAIL_PROVIDER, StubEmailProvider)()


def get_provider_for_channel(channel: str) -> MessageProvider:
    return {
        "whatsapp": get_whatsapp_provider,
        "sms": get_sms_provider,
        "email": get_email_provider,
    }[channel]()
