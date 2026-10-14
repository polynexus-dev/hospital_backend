"""
Pluggable send-side adapters, one per channel, picked by settings.*_PROVIDER:
- WhatsApp: AWSEndUserMessagingWhatsAppProvider (WHATSAPP_PROVIDER=aws);
- SMS: MSG91SMSProvider (SMS_PROVIDER=msg91), DLT templates via MSG91 Flow;
- email: SMTPEmailProvider (EMAIL_PROVIDER=smtp, or polymx), sending
  through Polynexus's polyMX mail service with Django's EMAIL_* settings.
Each defaults to a stub that logs instead of sending.

send() gets the rendered text plus, when the message came from a Template,
the template and its context: SMS (DLT) and WhatsApp outside the 24-hour
window must send the provider-registered template, not free text.
"""
import logging
from abc import ABC, abstractmethod

from django.conf import settings

logger = logging.getLogger("communications.adapters")


def _redact_contact(value: str) -> str:
    """Masks a phone number/email address for logging (Part A #3 — no
    patient data in logs by default). Keeps just enough to recognize "same
    contact" across two log lines while debugging, without putting the
    full identifier in the log stream."""
    if not value:
        return value
    if "@" in value:
        local, _, domain = value.partition("@")
        return f"{local[:2]}***@{domain}"
    return f"***{value[-4:]}" if len(value) > 4 else "***"


class MessageProvider(ABC):
    @abstractmethod
    def send(self, *, to: str, body: str, subject: str = "", template=None, context=None, headers=None) -> str:
        """Sends the message, returns the provider's message id. Raises when
        the provider refuses it."""


class SendRefused(Exception):
    """The message can't be sent as it stands (e.g. an SMS template with no
    DLT registration); the reason is shown to staff."""


def _template_values(template, context):
    context = context or {}
    return [str(context.get(name, "")) for name in template.variables()]


class StubWhatsAppProvider(MessageProvider):
    """No provider configured — logs instead of sending."""

    def send(self, *, to: str, body: str, subject: str = "", template=None, context=None, headers=None) -> str:
        logger.info("STUB WhatsApp -> %s (%d chars)", _redact_contact(to), len(body))
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

    def send(self, *, to: str, body: str, subject: str = "", template=None, context=None, headers=None) -> str:
        import json

        import boto3

        client = boto3.client("socialmessaging", region_name=settings.WHATSAPP_AWS_REGION)
        if template is not None and template.provider_template_id:
            # Meta-approved template: the only kind WhatsApp delivers outside
            # the 24-hour window after the patient last wrote to us.
            message_payload = {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "template",
                "template": {
                    "name": template.provider_template_id,
                    "language": {"code": template.language},
                    "components": [{"type": "body", "parameters": [{"type": "text", "text": v or "-"} for v in _template_values(template, context)]}],
                },
            }
        else:
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
        logger.info("AWS WhatsApp -> %s: message_id=%s", _redact_contact(to), message_id)
        return message_id


class StubSMSProvider(MessageProvider):
    """Stands in for a DLT-registered SMS route."""

    def send(self, *, to: str, body: str, subject: str = "", template=None, context=None, headers=None) -> str:
        logger.info("STUB SMS -> %s (%d chars)", _redact_contact(to), len(body))
        return f"stub-sms-{to}"


class MSG91SMSProvider(MessageProvider):
    """SMS through MSG91's Flow API. Indian SMS must match a DLT-registered
    template, so free text is refused: the Template needs its MSG91
    template id (provider_template_id) and the variable names MSG91 expects
    (provider_variables). Settings: MSG91_AUTH_KEY, MSG91_FLOW_URL.
    Delivery reports come back to apps.communications.delivery."""

    def send(self, *, to: str, body: str, subject: str = "", template=None, context=None, headers=None) -> str:
        import requests

        if not settings.MSG91_AUTH_KEY:
            raise SendRefused("SMS isn't set up on this server (MSG91_AUTH_KEY).")
        if template is None or not template.provider_template_id:
            raise SendRefused("This SMS template has no MSG91 / DLT template id yet (Templates > edit).")
        digits = "".join(ch for ch in to if ch.isdigit())[-10:]
        if len(digits) != 10:
            raise SendRefused("Not a valid Indian mobile number.")
        context = context or {}
        recipient = {"mobiles": f"91{digits}", **{name: str(context.get(name, "")) for name in template.variables()}}
        response = requests.post(
            settings.MSG91_FLOW_URL,
            json={"template_id": template.provider_template_id, "short_url": "0", "recipients": [recipient]},
            headers={"authkey": settings.MSG91_AUTH_KEY, "accept": "application/json", "content-type": "application/json"},
            timeout=15,
        )
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400 or data.get("type") != "success":
            raise RuntimeError(f"MSG91 refused the SMS: {str(data.get('message') or response.status_code)[:150]}")
        request_id = str(data.get("message") or data.get("request_id") or "")
        logger.info("MSG91 SMS -> %s: request_id=%s", _redact_contact(to), request_id)
        return request_id


class StubEmailProvider(MessageProvider):
    """Stands in for polyMX."""

    def send(self, *, to: str, body: str, subject: str = "", template=None, context=None, headers=None) -> str:
        logger.info("STUB Email -> %s (%d chars, subject %d chars)", _redact_contact(to), len(body), len(subject))
        return f"stub-email-{to}"


class SMTPEmailProvider(MessageProvider):
    """Email through polyMX (Polynexus's mail service) or any SMTP server:
    Django's EMAIL_HOST / EMAIL_PORT / EMAIL_HOST_USER / EMAIL_HOST_PASSWORD
    / EMAIL_USE_TLS, sent from MESSAGING_FROM_EMAIL. Plain text plus a
    simple HTML copy; `headers` carries List-Unsubscribe for marketing."""

    def send(self, *, to: str, body: str, subject: str = "", template=None, context=None, headers=None) -> str:
        from django.core.mail import EmailMultiAlternatives
        from django.core.mail.message import make_msgid
        from django.utils.html import escape, urlize

        from email.utils import formataddr, parseaddr

        default_name, address = parseaddr(settings.MESSAGING_FROM_EMAIL)
        sender = formataddr(((context or {}).get("hospital_name") or default_name, address))
        message_id = make_msgid(domain=address.rsplit("@", 1)[-1] or None)
        email = EmailMultiAlternatives(subject=subject or "Message from your hospital", body=body, from_email=sender,
                                       to=[to], headers={"Message-ID": message_id, **(headers or {})})
        html = "<br>".join(urlize(escape(line)) for line in body.splitlines())
        email.attach_alternative(f'<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.5">{html}</div>', "text/html")
        email.send(fail_silently=False)
        logger.info("Email -> %s: %s", _redact_contact(to), message_id)
        return message_id.strip("<>")


_WHATSAPP_PROVIDERS = {"stub": StubWhatsAppProvider, "aws": AWSEndUserMessagingWhatsAppProvider}
_SMS_PROVIDERS = {"stub": StubSMSProvider, "msg91": MSG91SMSProvider}
_EMAIL_PROVIDERS = {"stub": StubEmailProvider, "smtp": SMTPEmailProvider, "polymx": SMTPEmailProvider}


def get_whatsapp_provider() -> MessageProvider:
    from apps.licensing.service import outbound_allowed

    if not outbound_allowed("whatsapp"):
        return StubWhatsAppProvider()  # on-premise without the WhatsApp feature: never leaves the building
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
