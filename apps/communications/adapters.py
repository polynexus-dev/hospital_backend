"""
Pluggable send-side adapters, one per channel. WhatsApp now has a real
implementation (AWSEndUserMessagingWhatsAppProvider, set
WHATSAPP_PROVIDER=aws once AWS credentials/phone number are provisioned —
see that class's docstring). SMS (DLT-registered route) and email
(SES/Brevo) don't have a chosen vendor yet, so those two still default to a
stub that logs instead of sending. Wiring a real provider for them later
means adding one adapter class here and setting the corresponding
settings.*_PROVIDER value — no changes anywhere else in the codebase.
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
    """No provider configured — logs instead of sending."""

    def send(self, *, to: str, body: str, subject: str = "") -> str:
        logger.info("STUB WhatsApp -> %s: %s", to, body)
        return f"stub-whatsapp-{to}"


class AWSEndUserMessagingWhatsAppProvider(MessageProvider):
    """Real send path via AWS End User Messaging Social (Part A §15's
    intended provider). This proxies Meta's WhatsApp Cloud API through AWS,
    so the `message` payload is the same JSON body Meta's own Cloud API
    expects — see https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages.

    NOTE: this is a genuine implementation, not a stub. The three
    parameters below (originationPhoneNumberId, message, metaApiVersion)
    and the messageId return value were confirmed against the installed
    boto3 SDK's actual `socialmessaging` service model — this is not a
    guess. What's NOT yet verified is a real end-to-end send: that needs
    a linked WhatsApp Business Account in AWS End User Messaging Social, a
    registered origination phone number, and valid AWS credentials, none
    of which were available while building this. Test with a real send
    before relying on it. It fails loudly (raises) rather than pretending
    to succeed if misconfigured or if boto3/AWS reject the call — do not
    swallow the exception here, `communications.services.send_message`
    needs to know a send actually failed.

    Required settings (see config/settings/base.py):
      WHATSAPP_AWS_REGION, WHATSAPP_ORIGINATION_PHONE_NUMBER_ID,
      WHATSAPP_META_API_VERSION
    AWS credentials themselves are NOT read from Django settings — boto3's
    standard credential chain applies (env vars, ~/.aws/credentials, or an
    IAM role in production). Do not add AWS access keys to .env.
    """

    def send(self, *, to: str, body: str, subject: str = "") -> str:
        import json

        import boto3

        client = boto3.client("socialmessaging", region_name=settings.WHATSAPP_AWS_REGION)
        message_payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body},
        }
        response = client.send_whatsapp_message(
            originationPhoneNumberId=settings.WHATSAPP_ORIGINATION_PHONE_NUMBER_ID,
            metaApiVersion=settings.WHATSAPP_META_API_VERSION,
            message=json.dumps(message_payload).encode("utf-8"),
        )
        message_id = response.get("messageId", "")
        logger.info("AWS WhatsApp -> %s: message_id=%s", to, message_id)
        return message_id


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


_WHATSAPP_PROVIDERS = {"stub": StubWhatsAppProvider, "aws": AWSEndUserMessagingWhatsAppProvider}
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
