"""Reading insurer / TPA emails into the insurance inbox.

Each hospital can connect one mailbox (the address insurers write to). New
mail is fetched over IMAP (read-only: messages are not marked read) and
each email becomes a ClaimMessage:

* Matched to its case by, in order: a claim number in the subject or body,
  a pre-auth reference ("Pre-auth #123", "PA-123"), or a policy number of a
  live pre-auth.
* Typed from its subject (else its body): query, documents requested,
  approval, enhancement approval, rejection, settlement advice, or note.
* Queries, document requests and notes on a matched case take effect at
  once (the case goes on query, the reply-by date is set). Anything that
  moves money or closes a case (approvals, enhancements, rejections,
  settlements) and anything unmatched waits in the inbox's "to review"
  list for someone to confirm the case, type and amount.

The same email is never imported twice (Message-ID, then IMAP UID).
"""
import email
import imaplib
import logging
import re
from decimal import Decimal, InvalidOperation
from email import policy
from email.utils import parseaddr, parsedate_to_datetime

from django.core.files.base import ContentFile
from django.utils import timezone

from apps.core.encryption import compute_blind_index

from .models import Claim, ClaimMessage, PreAuthRequest

logger = logging.getLogger(__name__)
K = ClaimMessage.Kind

# Checked in this order; the subject is classified first, the body only if
# the subject says nothing recognisable.
KIND_WORDS = [
    (K.REJECTION, ("reject", "repudiat", "denied", "denial")),
    (K.SETTLEMENT, ("settlement", "settled", "payment advice", "utr", "remittance")),
    (K.ENHANCEMENT_APPROVAL, ("enhancement approv", "enhancement sanction", "enhanced amount", "additional amount approved")),
    (K.APPROVAL, ("approv", "authorization letter", "authorisation letter", "cashless authori", "sanction")),
    (K.QUERY, ("query", "queries", "clarification", "additional information", "information required")),
    (K.DOCUMENTS, ("document", "deficiency", "pending papers", "submit the following")),
]
NEEDS_REVIEW = {K.APPROVAL, K.ENHANCEMENT_APPROVAL, K.REJECTION, K.SETTLEMENT}
AMOUNT = re.compile(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d{1,2})?)", re.I)
PREAUTH_REF = re.compile(r"(?:pre[\s-]?auth(?:ori[sz]ation)?|\bPA)\s*(?:no\.?|id|ref\.?)?\s*[#:\-]?\s*(\d{1,9})\b", re.I)
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9/\-]{4,39}")


def classify(subject, body):
    for text in (subject or "", body or ""):
        low = text.lower()
        for kind, words in KIND_WORDS:
            if any(w in low for w in words):
                return kind
    return K.NOTE


def parse_amount(text):
    for m in AMOUNT.finditer(text or ""):
        try:
            value = Decimal(m.group(1).replace(",", ""))
        except InvalidOperation:
            continue
        if value > 0:
            return value
    return None


def match_case(hospital_id, text):
    """(pre-auth, claim) — at most one of them — or (None, None)."""
    tokens = {t.strip("-/") for t in TOKEN.findall(text or "")}
    if tokens:
        claim = Claim.objects.filter(hospital_id=hospital_id, claim_number__in=list(tokens)[:500]).exclude(claim_number="").order_by("-submitted_at").first()
        if claim is not None:
            return None, claim
    for m in PREAUTH_REF.finditer(text or ""):
        pa = PreAuthRequest.objects.filter(hospital_id=hospital_id, pk=int(m.group(1))).first()
        if pa is not None:
            return pa, None
    lookups = [compute_blind_index(t) for t in tokens if any(c.isdigit() for c in t)][:500]
    if lookups:
        pa = (PreAuthRequest.objects.filter(hospital_id=hospital_id, policy_number_lookup__in=lookups)
              .exclude(status=PreAuthRequest.Status.REJECTED).order_by("-submitted_at").first())
        if pa is not None:
            return pa, None
    return None, None


def _text(msg):
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = part.get_content()
    if part.get_content_type() == "text/html":
        content = re.sub(r"<[^>]+>", " ", content)
    return re.sub(r"[ \t]+", " ", content).strip()


def ingest(mailbox, raw):
    """One raw RFC 822 email -> ClaimMessage (or None if already imported)."""
    from .inbox import record

    msg = email.message_from_bytes(raw, policy=policy.default)
    message_id = (msg.get("Message-ID") or "").strip()[:255]
    if message_id and ClaimMessage.objects.filter(hospital_id=mailbox.hospital_id, email_message_id=message_id).exists():
        return None
    subject = str(msg.get("Subject") or "(no subject)")[:255]
    body = _text(msg)
    sender = parseaddr(str(msg.get("From") or ""))[1][:254]
    try:
        received = parsedate_to_datetime(str(msg.get("Date"))) if msg.get("Date") else timezone.now()
    except (TypeError, ValueError):
        received = timezone.now()
    if timezone.is_naive(received):
        received = timezone.make_aware(received)
    pa, claim = match_case(mailbox.hospital_id, f"{subject}\n{body}")
    kind = classify(subject, body)
    attachments = [a for a in msg.iter_attachments() if a.get_filename()]
    names = [a.get_filename() for a in attachments]
    m = ClaimMessage(
        hospital_id=mailbox.hospital_id, preauth_request=pa, claim=claim, direction=ClaimMessage.Direction.INCOMING, kind=kind,
        subject=subject, body=(body[:20000] + (f"\n\nAttachments: {', '.join(names)}" if names else "")),
        amount=parse_amount(f"{subject}\n{body}") if kind in NEEDS_REVIEW else None, received_at=received,
        source=ClaimMessage.Source.EMAIL, email_from=sender, email_message_id=message_id,
    )
    if attachments:
        first = attachments[0]
        m.attachment.save(first.get_filename()[:100], ContentFile(first.get_payload(decode=True) or b""), save=False)
    if (pa or claim) and kind not in NEEDS_REVIEW:
        return record(m)  # queries / documents / notes take effect now
    m.needs_review = True
    m.save()
    return m


def poll(mailbox, connect=None):
    """Fetches mail newer than the last imported UID. Returns how many emails were imported."""
    connect = connect or (lambda: (imaplib.IMAP4_SSL if mailbox.use_ssl else imaplib.IMAP4)(mailbox.host, mailbox.port))
    imported = 0
    try:
        imap = connect()
        imap.login(mailbox.username, mailbox.password)
        imap.select(mailbox.folder or "INBOX", readonly=True)
        _typ, data = imap.uid("search", None, f"UID {mailbox.last_uid + 1}:*")
        uids = sorted(int(u) for u in (data[0] or b"").split() if int(u) > mailbox.last_uid)
        for uid in uids:
            _typ, parts = imap.uid("fetch", str(uid), "(BODY.PEEK[])")
            raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
            if raw and ingest(mailbox, raw) is not None:
                imported += 1
            mailbox.last_uid = uid
        try:
            imap.logout()
        except Exception:  # noqa: BLE001 - the work is done; a failed goodbye doesn't matter
            pass
        mailbox.last_error = ""
    except Exception as exc:  # noqa: BLE001 - recorded on the mailbox and retried next run
        logger.exception("TPA mailbox %s failed", mailbox.pk)
        mailbox.last_error = str(exc)[:255]
    mailbox.last_checked_at = timezone.now()
    mailbox.save(update_fields=["last_uid", "last_error", "last_checked_at"])
    return imported


def poll_all():
    from .models import TPAMailbox

    return sum(poll(mb) for mb in TPAMailbox.objects.filter(is_active=True))
