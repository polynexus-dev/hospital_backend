"""Manual vouchers with maker-checker approval, and the day book.

An accountant enters a journal / payment / receipt / contra voucher (it
must balance). When AccountsSettings.voucher_approval_required is on, it
waits as "pending" until someone else with finance.approve_journalentry
approves it; the maker can't approve their own. Only approved vouchers
count in the day book, trial balance and Tally export. A rejected voucher
can be corrected and resubmitted.
"""
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models_ar import AccountsSettings
from .models_fpm import JournalEntry, JournalLine

MANUAL_TYPES = ("journal", "payment", "receipt", "contra", "debit_note", "credit_note")
CENT = Decimal("0.01")


def fy_label(d):
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def next_number(hospital_id, prefix, d, model, field):
    """PREFIX/2026-27/0001 — sequential per financial year."""
    stem = f"{prefix}/{fy_label(d)}/"
    last = (model.objects.filter(hospital_id=hospital_id, **{f"{field}__startswith": stem}).order_by(f"-{field}").values_list(field, flat=True).first())
    n = int(last.rsplit("/", 1)[1]) + 1 if last else 1
    return f"{stem}{n:04d}"


VOUCHER_PREFIX = {"journal": "JV", "payment": "PV", "receipt": "RV", "contra": "CV", "debit_note": "DN", "credit_note": "CN"}


def clean_lines(hospital_id, raw):
    """[{account: code, debit, credit}] -> [(code, Decimal, Decimal)]; must balance."""
    from .accounting import ensure_accounts

    accounts = ensure_accounts(hospital_id)
    if not isinstance(raw, list) or len(raw) < 2:
        raise ValidationError({"lines": "At least two lines."})
    out = []
    for i, ln in enumerate(raw, start=1):
        code = str((ln or {}).get("account", "")).strip()
        if code not in accounts:
            raise ValidationError({"lines": f"Line {i}: no ledger account {code!r}."})
        try:
            dr, cr = Decimal(str(ln.get("debit") or 0)).quantize(CENT), Decimal(str(ln.get("credit") or 0)).quantize(CENT)
        except InvalidOperation:
            raise ValidationError({"lines": f"Line {i}: amounts must be numbers."})
        if dr < 0 or cr < 0 or (dr and cr) or not (dr or cr):
            raise ValidationError({"lines": f"Line {i}: enter either a debit or a credit."})
        out.append((code, dr, cr))
    dr_total, cr_total = sum(d for _, d, _ in out), sum(c for _, _, c in out)
    if dr_total != cr_total:
        raise ValidationError({"lines": f"Debits ({dr_total}) and credits ({cr_total}) must be equal."})
    return out


def _write_lines(entry, lines):
    from .accounting import ensure_accounts

    accounts = ensure_accounts(entry.hospital_id)
    entry.lines.all().delete()
    JournalLine.objects.bulk_create([JournalLine(entry=entry, account=accounts[c], debit=d, credit=cr) for c, d, cr in lines])


@transaction.atomic
def create_manual(hospital_id, user, *, voucher_type, entry_date, narration, party, lines):
    if voucher_type not in MANUAL_TYPES:
        raise ValidationError({"voucher_type": f"One of {', '.join(MANUAL_TYPES)}."})
    lines = clean_lines(hospital_id, lines)
    needs_approval = AccountsSettings.for_hospital(hospital_id).voucher_approval_required
    number = next_number(hospital_id, VOUCHER_PREFIX[voucher_type], entry_date, JournalEntry, "voucher_number")
    entry = JournalEntry.objects.create(
        hospital_id=hospital_id, voucher_type=voucher_type, voucher_number=number, entry_date=entry_date, narration=narration[:255],
        party_name=party[:200], source_type="manual", source_id=number, is_manual=True, created_by=user,
        status=JournalEntry.Status.PENDING if needs_approval else JournalEntry.Status.APPROVED,
        approved_by=None if needs_approval else user, approved_at=None if needs_approval else timezone.now(),
    )
    _write_lines(entry, lines)
    return entry


@transaction.atomic
def update_manual(entry, user, *, entry_date=None, narration=None, party=None, lines=None):
    if not entry.is_manual or entry.status == JournalEntry.Status.APPROVED:
        raise ValidationError({"status": "Only a pending or rejected manual voucher can be edited."})
    if lines is not None:
        _write_lines(entry, clean_lines(entry.hospital_id, lines))
    if entry_date:
        entry.entry_date = entry_date
    if narration is not None:
        entry.narration = narration[:255]
    if party is not None:
        entry.party_name = party[:200]
    entry.status, entry.rejection_reason = JournalEntry.Status.PENDING, ""  # (re)submitted
    entry.save()
    return entry


def approve(entry, user):
    if entry.status != JournalEntry.Status.PENDING:
        raise ValidationError({"status": "Only a pending voucher can be approved."})
    if entry.created_by_id == user.pk:
        raise ValidationError({"detail": "Someone other than the person who entered the voucher must approve it."})
    entry.status, entry.approved_by, entry.approved_at = JournalEntry.Status.APPROVED, user, timezone.now()
    entry.save(update_fields=["status", "approved_by", "approved_at"])
    return entry


def reject(entry, user, reason):
    if entry.status != JournalEntry.Status.PENDING:
        raise ValidationError({"status": "Only a pending voucher can be rejected."})
    if not reason:
        raise ValidationError({"reason": "Say why, so the maker can correct it."})
    entry.status, entry.rejection_reason = JournalEntry.Status.REJECTED, reason[:255]
    entry.save(update_fields=["status", "rejection_reason"])
    return entry


# --- day book ----------------------------------------------------------------------

def day_book(hospital_id, *, start, end, voucher_type="", account="", party="", status="approved", search=""):
    """Entries in the period with filters; returns (queryset, totals)."""
    qs = JournalEntry.objects.filter(hospital_id=hospital_id, entry_date__range=(start, end))
    if status and status != "all":
        qs = qs.filter(status=status)
    if voucher_type:
        qs = qs.filter(voucher_type=voucher_type)
    if account:
        qs = qs.filter(lines__account__code=account)
    if party:
        qs = qs.filter(party_name__icontains=party)
    if search:
        qs = qs.filter(Q(voucher_number__icontains=search) | Q(narration__icontains=search))
    qs = qs.distinct().order_by("entry_date", "id")
    totals = JournalLine.objects.filter(entry__in=qs.values("pk")).aggregate(debit=Sum("debit"), credit=Sum("credit"))
    by_type = {r["voucher_type"]: r["n"] for r in qs.order_by().values("voucher_type").annotate(n=Count("id", distinct=True))}
    return qs, {"debit": totals["debit"] or Decimal("0"), "credit": totals["credit"] or Decimal("0"), "vouchers": sum(by_type.values()), "by_type": by_type}


def entry_row(e):
    lines = [{"account_code": ln.account.code, "account": ln.account.name, "debit": ln.debit, "credit": ln.credit} for ln in e.lines.all()]
    return {
        "id": e.pk, "date": e.entry_date, "voucher_type": e.voucher_type, "voucher_number": e.voucher_number, "party": e.party_name,
        "narration": e.narration, "status": e.status, "is_manual": e.is_manual, "lines": lines,
        "debit": sum((ln["debit"] for ln in lines), Decimal("0")), "credit": sum((ln["credit"] for ln in lines), Decimal("0")),
        "created_by": e.created_by.get_full_name() if e.created_by_id else "", "approved_by": e.approved_by.get_full_name() if e.approved_by_id else "",
        "rejection_reason": e.rejection_reason,
    }
