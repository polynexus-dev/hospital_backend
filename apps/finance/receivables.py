"""Customer invoices, receipts, ledger, ageing and recurring invoices.

Postings (finance.accounting.post):
  issue    Dr 1120 Customer receivables (total) / Cr 4000 income (taxable) / Cr 2100 output GST
  cancel   the reverse, as a credit note (only while nothing has been received)
  receipt  Dr bank/cash (amount) + Dr 1310 TDS receivable / Cr 1120
GST: CGST + SGST when the customer's state is the hospital's (or either is
unknown), IGST otherwise.
"""
import calendar
import io
import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models_ar import AccountsSettings, Customer, CustomerInvoice, CustomerInvoiceLine, CustomerReceipt, RecurringInvoice
from .vouchers import next_number

logger = logging.getLogger(__name__)
CENT = Decimal("0.01")
ZERO = Decimal("0")


def q(x):
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


# --- invoices --------------------------------------------------------------------

def _clean_lines(hospital_id, raw):
    from .models_fpm import ServiceTariff

    if not isinstance(raw, list) or not raw:
        raise ValidationError({"lines": "Add at least one line."})
    out = []
    for i, ln in enumerate(raw, start=1):
        ln = ln or {}
        tariff = None
        if ln.get("tariff"):
            tariff = ServiceTariff.objects.filter(pk=ln["tariff"], hospital_id=hospital_id).first()
            if tariff is None:
                raise ValidationError({"lines": f"Line {i}: service not found."})
        try:
            qty = Decimal(str(ln.get("quantity") or 1))
            rate = Decimal(str(ln["rate"])) if ln.get("rate") not in (None, "") else (tariff.rate if tariff else None)
            discount = Decimal(str(ln.get("discount") or 0))
            gst = Decimal(str(ln["gst_rate"])) if ln.get("gst_rate") not in (None, "") else (tariff.gst_rate if tariff else ZERO)
        except Exception:  # noqa: BLE001 - any bad number
            raise ValidationError({"lines": f"Line {i}: quantity, rate, discount and GST must be numbers."})
        description = str(ln.get("description") or (tariff.name if tariff else "")).strip()
        if rate is None or not description:
            raise ValidationError({"lines": f"Line {i}: description and rate are required."})
        if qty <= 0 or rate < 0 or discount < 0 or gst not in (ZERO, Decimal("5"), Decimal("12"), Decimal("18"), Decimal("28")):
            raise ValidationError({"lines": f"Line {i}: check quantity, rate, discount and GST rate (0/5/12/18/28)."})
        taxable = q(qty * rate - discount)
        if taxable < 0:
            raise ValidationError({"lines": f"Line {i}: discount is more than the line value."})
        out.append({"tariff": tariff, "description": description[:255], "hsn_sac": str(ln.get("hsn_sac") or (tariff.hsn_sac if tariff else "") or "")[:10],
                    "quantity": qty, "rate": rate, "discount": discount, "gst_rate": gst, "taxable_amount": taxable, "tax_amount": q(taxable * gst / 100)})
    return out


def is_interstate(hospital_id, customer):
    own = AccountsSettings.for_hospital(hospital_id).state_code
    return bool(own and customer.state_code and own != customer.state_code)


def _set_totals(inv, lines):
    inv.taxable_amount = sum((ln["taxable_amount"] for ln in lines), ZERO)
    tax = sum((ln["tax_amount"] for ln in lines), ZERO)
    if inv.is_interstate:
        inv.cgst = inv.sgst = ZERO
        inv.igst = tax
    else:
        inv.cgst = q(tax / 2)
        inv.sgst = tax - inv.cgst
        inv.igst = ZERO
    inv.total_amount = inv.taxable_amount + tax


@transaction.atomic
def save_draft(hospital_id, user, *, customer, lines, invoice=None, invoice_date=None, due_date=None, notes="", recurring=None, recurring_period=None):
    if invoice is not None and invoice.status != CustomerInvoice.Status.DRAFT:
        raise ValidationError({"status": "Only a draft invoice can be edited."})
    cleaned = _clean_lines(hospital_id, lines)
    inv = invoice or CustomerInvoice(hospital_id=hospital_id, created_by=user, recurring=recurring, recurring_period=recurring_period)
    inv.customer = customer
    inv.invoice_date = invoice_date or inv.invoice_date or timezone.localdate()
    inv.due_date = due_date or inv.invoice_date + timedelta(days=customer.credit_days)
    inv.notes = notes or ""
    inv.is_interstate = is_interstate(hospital_id, customer)
    _set_totals(inv, cleaned)
    inv.save()
    inv.lines.all().delete()
    CustomerInvoiceLine.objects.bulk_create([CustomerInvoiceLine(invoice=inv, **ln) for ln in cleaned])
    return inv


def _post_invoice(inv, *, reverse=False):
    from .accounting import post

    tax = inv.cgst + inv.sgst + inv.igst
    lines = [("1120", inv.total_amount, 0), ("4000", 0, inv.taxable_amount)]
    if tax:
        lines.append(("2100", 0, tax))
    if reverse:
        lines = [(code, cr, dr) for code, dr, cr in lines]
    return post(inv.hospital_id, voucher_type="credit_note" if reverse else "sales", voucher_number=inv.invoice_number if not reverse else f"CN-{inv.invoice_number}",
                source_type="customer_invoice", source_id=inv.pk, lines=lines,
                narration=(f"Cancelled: {inv.cancelled_reason}" if reverse else f"Invoice {inv.invoice_number}")[:255],
                party=inv.customer.name, entry_date=timezone.localdate() if reverse else inv.invoice_date)


@transaction.atomic
def issue(inv):
    if inv.status != CustomerInvoice.Status.DRAFT:
        raise ValidationError({"status": "Only a draft can be issued."})
    if not inv.lines.exists():
        raise ValidationError({"lines": "The invoice has no lines."})
    prefix = AccountsSettings.for_hospital(inv.hospital_id).invoice_prefix or "INV"
    for _ in range(3):
        inv.invoice_number = next_number(inv.hospital_id, prefix, inv.invoice_date, CustomerInvoice, "invoice_number")
        try:
            with transaction.atomic():
                inv.status, inv.issued_at = CustomerInvoice.Status.ISSUED, timezone.now()
                inv.save(update_fields=["invoice_number", "status", "issued_at"])
            break
        except IntegrityError:
            continue
    _post_invoice(inv)
    return inv


@transaction.atomic
def cancel(inv, reason):
    if inv.status == CustomerInvoice.Status.CANCELLED:
        raise ValidationError({"status": "Already cancelled."})
    if inv.amount_received > 0:
        raise ValidationError({"status": "Money has been received against this invoice; reverse the receipt first."})
    if not reason:
        raise ValidationError({"reason": "Give a reason."})
    was_issued = inv.status != CustomerInvoice.Status.DRAFT
    inv.status, inv.cancelled_reason, inv.cancelled_at = CustomerInvoice.Status.CANCELLED, reason[:255], timezone.now()
    inv.save(update_fields=["status", "cancelled_reason", "cancelled_at"])
    if was_issued:
        _post_invoice(inv, reverse=True)
    return inv


def refresh_status(inv):
    received = sum((r.amount + r.tds_amount for r in inv.receipts.all()), ZERO)
    inv.amount_received = received
    if inv.status not in (CustomerInvoice.Status.DRAFT, CustomerInvoice.Status.CANCELLED):
        inv.status = CustomerInvoice.Status.PAID if received >= inv.total_amount else (
            CustomerInvoice.Status.PARTIALLY_PAID if received > 0 else CustomerInvoice.Status.ISSUED)
    inv.save(update_fields=["amount_received", "status"])


@transaction.atomic
def record_receipt(hospital_id, user, *, customer, invoice=None, amount, tds_amount=ZERO, mode="neft", reference="", received_on=None, notes=""):
    from .accounting import post

    amount, tds_amount = q(amount), q(tds_amount or 0)
    if amount <= 0 or tds_amount < 0:
        raise ValidationError({"amount": "Must be more than 0."})
    if mode not in CustomerReceipt.Mode.values:
        raise ValidationError({"mode": "neft | upi | cheque | cash"})
    if mode != "cash" and not reference:
        raise ValidationError({"reference": "UTR / cheque number is required."})
    if invoice is not None:
        invoice = CustomerInvoice.objects.select_for_update().get(pk=invoice.pk)
        if invoice.customer_id != customer.pk:
            raise ValidationError({"invoice": "That invoice is for another customer."})
        if invoice.status in (CustomerInvoice.Status.DRAFT, CustomerInvoice.Status.CANCELLED):
            raise ValidationError({"invoice": "Issue the invoice before receiving against it."})
        if amount + tds_amount > invoice.outstanding + CENT:
            raise ValidationError({"amount": f"More than the outstanding ₹{invoice.outstanding}."})
    r = CustomerReceipt.objects.create(hospital_id=hospital_id, customer=customer, invoice=invoice, amount=amount, tds_amount=tds_amount, mode=mode,
                                       reference=reference[:60], received_on=received_on or timezone.localdate(), notes=notes[:255], received_by=user)
    bank = "1000" if mode == "cash" else "1010"
    post(hospital_id, voucher_type="receipt", voucher_number=f"CR{r.pk}", source_type="customer_receipt", source_id=r.pk,
         lines=[(bank, amount, 0), ("1310", tds_amount, 0), ("1120", 0, amount + tds_amount)],
         narration=f"Receipt {reference or mode}" + (f" against {invoice.invoice_number}" if invoice else " on account"), party=customer.name, entry_date=r.received_on)
    if invoice is not None:
        refresh_status(invoice)
    return r


# --- ledger and ageing ---------------------------------------------------------------

def customer_ledger(customer, start, end):
    """Opening balance, Dr invoices / Cr receipts and cancellations, running balance."""
    events = []
    for inv in customer.invoices.exclude(status=CustomerInvoice.Status.DRAFT):
        events.append((inv.invoice_date, 0, f"Invoice {inv.invoice_number}", inv.total_amount, ZERO, "invoice", inv.pk))
        if inv.status == CustomerInvoice.Status.CANCELLED:
            when = timezone.localdate(inv.cancelled_at) if inv.cancelled_at else inv.invoice_date
            events.append((when, 1, f"Cancelled {inv.invoice_number}: {inv.cancelled_reason}", ZERO, inv.total_amount, "cancellation", inv.pk))
    for r in customer.receipts.select_related("invoice"):
        label = f"Receipt {r.reference or r.get_mode_display()}" + (f" ({r.invoice.invoice_number})" if r.invoice_id else " on account")
        events.append((r.received_on, 2, label, ZERO, r.amount, "receipt", r.pk))
        if r.tds_amount:
            events.append((r.received_on, 3, f"TDS deducted by customer ({r.reference or r.get_mode_display()})", ZERO, r.tds_amount, "tds", r.pk))
    events.sort(key=lambda e: (e[0], e[1], e[6]))
    opening = sum((dr - cr for d, _, _, dr, cr, _, _ in events if d < start), ZERO)
    balance, rows = opening, []
    for d, _, text, dr, cr, kind, ref in events:
        if start <= d <= end:
            balance += dr - cr
            rows.append({"date": d, "particulars": text, "debit": dr, "credit": cr, "balance": balance, "kind": kind, "ref": ref})
    return {"customer": customer.name, "opening": opening, "rows": rows, "closing": balance,
            "debit": sum((r["debit"] for r in rows), ZERO), "credit": sum((r["credit"] for r in rows), ZERO)}


BUCKETS = (("current", None), ("1_30", 30), ("31_60", 60), ("61_90", 90), ("over_90", None))


def ageing(hospital_id, as_of=None):
    """Outstanding per customer by days past due."""
    as_of = as_of or timezone.localdate()
    out = defaultdict(lambda: {k: ZERO for k, _ in BUCKETS} | {"total": ZERO, "on_account": ZERO})
    names = {}
    for inv in CustomerInvoice.objects.filter(hospital_id=hospital_id, status__in=["issued", "partially_paid"]).select_related("customer"):
        due = inv.outstanding
        if not due:
            continue
        days = (as_of - (inv.due_date or inv.invoice_date)).days
        key = "current" if days <= 0 else "1_30" if days <= 30 else "31_60" if days <= 60 else "61_90" if days <= 90 else "over_90"
        row = out[inv.customer_id]
        row[key] += due
        row["total"] += due
        names[inv.customer_id] = inv.customer.name
    for r in CustomerReceipt.objects.filter(hospital_id=hospital_id, invoice__isnull=True).select_related("customer"):
        row = out[r.customer_id]
        row["on_account"] += r.amount + r.tds_amount
        row["total"] -= r.amount + r.tds_amount
        names[r.customer_id] = r.customer.name
    rows = [{"customer": cid, "customer_name": names[cid], **vals} for cid, vals in out.items()]
    rows.sort(key=lambda r: -r["total"])
    totals = {k: sum((r[k] for r in rows), ZERO) for k in [b for b, _ in BUCKETS] + ["on_account", "total"]}
    return {"as_of": as_of, "rows": rows, "totals": totals}


# --- recurring ------------------------------------------------------------------------

MONTHS = {"monthly": 1, "quarterly": 3, "half_yearly": 6, "yearly": 12}


def add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def advance(rec):
    """Next run, kept on the start date's day of month (31st -> month end)."""
    k = 1
    while True:
        nxt = add_months(rec.start_date, MONTHS[rec.frequency] * k)
        if nxt > rec.next_run_date:
            return nxt
        k += 1


def run_recurring(today=None):
    """Raises every due recurring invoice; catches up missed periods.
    Idempotent: one invoice per recurring template per run date."""
    today = today or timezone.localdate()
    made = 0
    for rec in RecurringInvoice.objects.filter(is_active=True, next_run_date__lte=today).select_related("customer"):
        while rec.next_run_date <= today and (rec.end_date is None or rec.next_run_date <= rec.end_date):
            period = rec.next_run_date
            try:
                with transaction.atomic():
                    inv = save_draft(rec.hospital_id, rec.created_by, customer=rec.customer, lines=rec.lines, invoice_date=period,
                                     notes=rec.notes, recurring=rec, recurring_period=period)
                    if rec.auto_issue:
                        issue(inv)
                made += 1
                if rec.auto_issue and rec.auto_email and rec.customer.email:
                    try:
                        email_invoice(inv)
                    except Exception:  # noqa: BLE001 - the invoice stands; staff can resend
                        logger.exception("Emailing recurring invoice %s failed", inv.pk)
            except IntegrityError:
                pass  # already raised for this period
            except ValidationError as exc:
                logger.warning("Recurring invoice %s couldn't run: %s", rec.pk, exc.detail)
                break
            rec.next_run_date = advance(rec)
        if rec.end_date and rec.next_run_date > rec.end_date:
            rec.is_active = False
        rec.last_run_at = timezone.now()
        rec.save(update_fields=["next_run_date", "is_active", "last_run_at"])
    return made


# --- PDF and email ------------------------------------------------------------------------

def money(x):
    return f"{Decimal(x):,.2f}"


def invoice_pdf(inv) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    acc = AccountsSettings.for_hospital(inv.hospital_id)
    hospital, c = inv.hospital, inv.customer
    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small", fontSize=8, leading=10)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
                            title=f"Tax invoice {inv.invoice_number}")
    title = "TAX INVOICE" if inv.status != CustomerInvoice.Status.DRAFT else "DRAFT INVOICE (not issued)"
    if inv.status == CustomerInvoice.Status.CANCELLED:
        title = "CANCELLED INVOICE"
    seller = (f"<b>{acc.legal_name or hospital.name}</b><br/>{(hospital.address or '').replace(chr(10), '<br/>')}"
              f"{f'<br/>GSTIN: {acc.gstin}' if acc.gstin else ''}{f'<br/>PAN: {acc.pan}' if acc.pan else ''}")
    buyer = (f"<b>Bill to: {c.name}</b><br/>{(c.billing_address or '').replace(chr(10), '<br/>')}"
             f"{f'<br/>GSTIN: {c.gstin}' if c.gstin else ''}{f'<br/>State code: {c.state_code}' if c.state_code else ''}")
    meta = (f"Invoice no.: <b>{inv.invoice_number or '-'}</b><br/>Date: {inv.invoice_date:%d %b %Y}<br/>"
            f"Due: {inv.due_date:%d %b %Y}<br/>Place of supply: {c.state_code or acc.state_code or '-'}")
    story = [Paragraph(title, styles["Title"]),
             Table([[Paragraph(seller, small), Paragraph(meta, small)], [Paragraph(buyer, small), ""]], colWidths=[120 * mm, 62 * mm],
                   style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.4, colors.grey)])),
             Spacer(1, 5 * mm)]
    tax_head = ["IGST"] if inv.is_interstate else ["CGST", "SGST"]
    rows = [["#", "Description", "HSN/SAC", "Qty", "Rate", "Disc.", "Taxable", "GST %"] + tax_head]
    for i, ln in enumerate(inv.lines.all(), start=1):
        tax = [money(ln.tax_amount)] if inv.is_interstate else [money(q(ln.tax_amount / 2)), money(ln.tax_amount - q(ln.tax_amount / 2))]
        rows.append([i, Paragraph(ln.description, small), ln.hsn_sac, f"{ln.quantity:g}", money(ln.rate), money(ln.discount) if ln.discount else "",
                     money(ln.taxable_amount), f"{ln.gst_rate:g}"] + tax)
    widths = [7, 55, 16, 11, 20, 14, 22, 11] + ([24] if inv.is_interstate else [12, 12])
    t = Table(rows, colWidths=[w * mm for w in widths], repeatRows=1)
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3864")),
                           ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                           ("ALIGN", (3, 1), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    totals = [["Taxable value", money(inv.taxable_amount)]]
    totals += [["IGST", money(inv.igst)]] if inv.is_interstate else [["CGST", money(inv.cgst)], ["SGST", money(inv.sgst)]]
    totals.append(["Invoice total (Rs.)", money(inv.total_amount)])
    if inv.amount_received:
        totals += [["Received", money(inv.amount_received)], ["Balance due", money(inv.outstanding)]]
    t2 = Table(totals, colWidths=[45 * mm, 35 * mm], hAlign="RIGHT")
    t2.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("LINEABOVE", (0, -1), (-1, -1), 0.5, colors.black)]))
    story += [t, Spacer(1, 4 * mm), t2, Spacer(1, 6 * mm)]
    if inv.notes:
        story.append(Paragraph(f"<b>Notes:</b> {inv.notes}", small))
    if acc.bank_details:
        story.append(Paragraph(f"<b>Bank details:</b><br/>{acc.bank_details.replace(chr(10), '<br/>')}", small))
    if acc.invoice_terms:
        story.append(Paragraph(f"<b>Terms:</b> {acc.invoice_terms}", small))
    story += [Spacer(1, 10 * mm), Paragraph(f"For {acc.legal_name or hospital.name}<br/><br/>Authorised signatory", small)]
    doc.build(story)
    return buf.getvalue()


def email_invoice(inv, to=None):
    from django.core.mail import EmailMessage

    to = to or inv.customer.email
    if not to:
        raise ValidationError({"email": "The customer has no email address."})
    if inv.status in (CustomerInvoice.Status.DRAFT, CustomerInvoice.Status.CANCELLED):
        raise ValidationError({"status": "Only an issued invoice can be emailed."})
    acc = AccountsSettings.for_hospital(inv.hospital_id)
    name = acc.legal_name or inv.hospital.name
    msg = EmailMessage(subject=f"Invoice {inv.invoice_number} from {name}",
                       body=(f"Dear {inv.customer.contact_person or inv.customer.name},\n\nPlease find attached invoice {inv.invoice_number} dated "
                             f"{inv.invoice_date:%d %b %Y} for Rs. {money(inv.total_amount)}, due on {inv.due_date:%d %b %Y}.\n\n{name} Accounts"),
                       from_email=settings.MESSAGING_FROM_EMAIL, to=[to])
    msg.attach(f"Invoice_{inv.invoice_number.replace('/', '-')}.pdf", invoice_pdf(inv), "application/pdf")
    msg.send(fail_silently=False)
    CustomerInvoice.objects.filter(pk=inv.pk).update(emailed_at=timezone.now())
    return to


def customer_for(hospital_id, pk):
    c = Customer.objects.filter(pk=pk, hospital_id=hospital_id).first()
    if c is None:
        raise ValidationError({"customer": "Not found."})
    return c
