"""Insurance inbox: correspondence on pre-auths and claims, and what needs
doing about it.

Recording an incoming message moves the case on: a query or document
request puts it on query (and is open until we reply); an approval,
enhancement approval, rejection or settlement advice updates the amounts
and status. A reply closes the query it answers (or the case's oldest open
one).
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import Claim, ClaimMessage, PreAuthRequest

K = ClaimMessage.Kind
INCOMING_KINDS = {K.QUERY, K.DOCUMENTS, K.APPROVAL, K.ENHANCEMENT_APPROVAL, K.REJECTION, K.SETTLEMENT, K.NOTE}
OUTGOING_KINDS = {K.REPLY, K.ENHANCEMENT_REQUEST, K.NOTE}
CLAIM_AGEING_DAYS = 30


def _case(msg):
    return msg.preauth_request or msg.claim


def _tpa(msg):
    return _case(msg).tpa_company


@transaction.atomic
def record(msg):
    """Validates and saves `msg`, applying its effect on the pre-auth / claim."""
    if bool(msg.preauth_request_id) == bool(msg.claim_id):
        raise ValidationError({"detail": "A message belongs to one pre-auth or one claim."})
    allowed = INCOMING_KINDS if msg.direction == ClaimMessage.Direction.INCOMING else OUTGOING_KINDS
    if msg.kind not in allowed:
        raise ValidationError({"kind": f"Not a {msg.get_direction_display().lower()} message type."})
    needs_amount = {K.APPROVAL, K.ENHANCEMENT_APPROVAL, K.SETTLEMENT}
    if msg.direction == ClaimMessage.Direction.INCOMING and msg.kind in needs_amount and (msg.amount is None or msg.amount < 0):
        raise ValidationError({"amount": "Enter the amount."})
    if msg.kind in (K.APPROVAL, K.ENHANCEMENT_APPROVAL) and not msg.preauth_request_id:
        raise ValidationError({"kind": "Approvals are recorded on the pre-auth."})
    if msg.kind == K.SETTLEMENT and not msg.claim_id:
        raise ValidationError({"kind": "Settlement advice is recorded on the claim."})
    if msg.direction == ClaimMessage.Direction.INCOMING and msg.kind in (K.QUERY, K.DOCUMENTS) and msg.respond_by is None:
        msg.respond_by = timezone.localdate(msg.received_at) + timedelta(days=max(_tpa(msg).avg_tat_days, 1))
    msg.save()
    if msg.direction == ClaimMessage.Direction.INCOMING:
        _apply_incoming(msg)
    elif msg.kind == K.REPLY:
        _close_query(msg)
    return msg


def _apply_incoming(msg):
    now = timezone.now()
    if msg.preauth_request_id:
        pa = PreAuthRequest.objects.select_for_update().get(pk=msg.preauth_request_id)
        if msg.kind in (K.QUERY, K.DOCUMENTS):
            pa.status = PreAuthRequest.Status.QUERY_RAISED
        elif msg.kind == K.APPROVAL:
            pa.status, pa.approved_amount, pa.approved_at = PreAuthRequest.Status.APPROVED, msg.amount, now
        elif msg.kind == K.ENHANCEMENT_APPROVAL:
            pa.status, pa.approved_amount, pa.approved_at = PreAuthRequest.Status.APPROVED, Decimal(pa.approved_amount or 0) + msg.amount, now
        elif msg.kind == K.REJECTION:
            pa.status = PreAuthRequest.Status.REJECTED
        pa.save()
    else:
        c = Claim.objects.select_for_update().get(pk=msg.claim_id)
        if msg.kind in (K.QUERY, K.DOCUMENTS):
            c.status = Claim.Status.UNDER_REVIEW
        elif msg.kind == K.REJECTION:
            c.status, c.rejection_reason = Claim.Status.REJECTED, msg.subject[:255]
        elif msg.kind == K.SETTLEMENT:
            c.settled_amount = msg.amount
            c.status = Claim.Status.SETTLED if msg.amount >= c.billed_amount else Claim.Status.PARTIALLY_SETTLED
            c.settled_at = now
        c.save()


def _close_query(reply):
    target = reply.in_reply_to
    if target is None:
        target = open_messages(reply.hospital_id).filter(preauth_request_id=reply.preauth_request_id, claim_id=reply.claim_id).order_by("received_at").first()
    elif not target.is_open or (target.preauth_request_id, target.claim_id) != (reply.preauth_request_id, reply.claim_id):
        raise ValidationError({"in_reply_to": "Not an open query on this case."})
    if target is None:
        return
    target.resolved_at = reply.received_at
    target.save(update_fields=["resolved_at"])
    if reply.in_reply_to_id is None:
        reply.in_reply_to = target
        reply.save(update_fields=["in_reply_to"])
    if reply.preauth_request_id and not open_messages(reply.hospital_id).filter(preauth_request_id=reply.preauth_request_id).exists():
        PreAuthRequest.objects.filter(pk=reply.preauth_request_id, status=PreAuthRequest.Status.QUERY_RAISED).update(status=PreAuthRequest.Status.SUBMITTED)


def open_messages(hospital_id):
    return ClaimMessage.objects.filter(hospital_id=hospital_id, direction=ClaimMessage.Direction.INCOMING, needs_review=False,
                                       kind__in=[K.QUERY, K.DOCUMENTS], resolved_at__isnull=True)


@transaction.atomic
def confirm(msg, user, *, preauth_request=None, claim=None, kind=None, amount=None):
    """Confirms an imported email waiting for review: its case, type and
    amount — then it takes effect like any recorded message."""
    if not msg.needs_review:
        raise ValidationError({"detail": "This message isn't waiting for review."})
    if preauth_request is not None or claim is not None:
        msg.preauth_request, msg.claim = preauth_request, claim
    if kind:
        msg.kind = kind
    if amount not in (None, ""):
        msg.amount = Decimal(str(amount))
    msg.needs_review = False
    msg.recorded_by = user
    return record(msg)


def dismiss(msg, user):
    """Not insurance correspondence (or a duplicate): kept, out of the review list."""
    msg.needs_review, msg.kind, msg.recorded_by = False, K.NOTE, user
    msg.save(update_fields=["needs_review", "kind", "recorded_by"])
    return msg


def _case_label(pa=None, claim=None):
    if pa is not None:
        return {"type": "pre_auth", "id": pa.pk, "patient": pa.patient.full_name, "tpa": pa.tpa_company.name, "status": pa.status, "amount": pa.claim_amount}
    return {"type": "claim", "id": claim.pk, "patient": claim.patient.full_name, "tpa": claim.tpa_company.name, "status": claim.status,
            "amount": claim.billed_amount, "claim_number": claim.claim_number}


def inbox(hospital_id, tpa_id=None):
    """What needs action, oldest first:
    - open insurer queries / document requests (overdue past their reply-by date);
    - pre-auths submitted with no answer beyond the TPA's usual turnaround;
    - claims submitted or under review for more than 30 days."""
    today = timezone.localdate()
    now = timezone.now()
    q = open_messages(hospital_id).select_related("preauth_request__patient", "preauth_request__tpa_company", "claim__patient", "claim__tpa_company")
    if tpa_id:
        q = q.filter(preauth_request__tpa_company_id=tpa_id) | q.filter(claim__tpa_company_id=tpa_id)
    queries = [{
        "message": m.pk, "kind": m.kind, "subject": m.subject, "received_at": m.received_at, "respond_by": m.respond_by,
        "overdue": bool(m.respond_by and m.respond_by < today), "case": _case_label(m.preauth_request, m.claim),
    } for m in q.order_by("respond_by", "received_at")]

    pas = PreAuthRequest.objects.filter(hospital_id=hospital_id, status=PreAuthRequest.Status.SUBMITTED).select_related("patient", "tpa_company")
    if tpa_id:
        pas = pas.filter(tpa_company_id=tpa_id)
    awaiting = []
    for pa in pas:
        last = pa.messages.order_by("-received_at").values_list("received_at", flat=True).first() or pa.submitted_at
        waited = (now - last).total_seconds() / 86400
        if waited > pa.tpa_company.avg_tat_days:
            awaiting.append({"case": _case_label(pa=pa), "waiting_days": round(waited, 1), "tat_days": pa.tpa_company.avg_tat_days})

    claims = Claim.objects.filter(hospital_id=hospital_id, status__in=[Claim.Status.SUBMITTED, Claim.Status.UNDER_REVIEW],
                                  submitted_at__lt=now - timedelta(days=CLAIM_AGEING_DAYS)).select_related("patient", "tpa_company")
    if tpa_id:
        claims = claims.filter(tpa_company_id=tpa_id)
    ageing = [{"case": _case_label(claim=c), "days": (now - c.submitted_at).days} for c in claims.order_by("submitted_at")]
    review = ClaimMessage.objects.filter(hospital_id=hospital_id, needs_review=True).select_related(
        "preauth_request__patient", "preauth_request__tpa_company", "claim__patient", "claim__tpa_company").order_by("received_at")
    to_review = [{
        "message": m.pk, "kind": m.kind, "subject": m.subject, "email_from": m.email_from, "received_at": m.received_at, "amount": m.amount,
        "case": _case_label(m.preauth_request, m.claim) if (m.preauth_request_id or m.claim_id) else None,
    } for m in review]
    return {
        "counts": {"open_queries": len(queries), "overdue_queries": sum(1 for x in queries if x["overdue"]),
                   "preauths_awaiting": len(awaiting), "claims_ageing": len(ageing), "to_review": len(to_review)},
        "queries": queries, "preauths_awaiting": sorted(awaiting, key=lambda x: -x["waiting_days"]), "claims_ageing": ageing,
        "to_review": to_review,
    }
