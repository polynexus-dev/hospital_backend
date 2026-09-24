"""
Double-entry posting, Tally export and GST reports.

Every financial document posts one balanced JournalEntry (idempotent on
source_type/source_id/voucher_type), so the trial balance always ties out
and the same vouchers can be pushed to Tally Prime as XML (Import Data →
Vouchers) instead of being re-keyed by the accounts team.
"""
from collections import defaultdict
from decimal import Decimal
from xml.sax.saxutils import escape

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models_fpm import JournalEntry, JournalLine, LedgerAccount

DEFAULT_ACCOUNTS = [
    ("1000", "Cash in hand", "asset", "Cash-in-Hand"),
    ("1010", "Bank account", "asset", "Bank Accounts"),
    ("1100", "Patient receivables", "asset", "Sundry Debtors"),
    ("1110", "Insurance / TPA receivables", "asset", "Sundry Debtors"),
    ("1200", "Inventory", "asset", "Stock-in-Hand"),
    ("1300", "Input GST", "asset", "Duties & Taxes"),
    ("1310", "TDS receivable", "asset", "Duties & Taxes"),
    ("2000", "Sundry creditors (vendors)", "liability", "Sundry Creditors"),
    ("2100", "Output GST", "liability", "Duties & Taxes"),
    ("2110", "TDS payable", "liability", "Duties & Taxes"),
    ("3000", "Capital", "equity", "Capital Account"),
    ("4000", "Hospital service income", "income", "Sales Accounts"),
    ("4100", "Pharmacy sales", "income", "Sales Accounts"),
    ("4900", "Discount allowed", "expense", "Indirect Expenses"),
    ("5000", "Purchases", "expense", "Purchase Accounts"),
    ("5900", "Claim disallowances written off", "expense", "Indirect Expenses"),
]

PAYMENT_ACCOUNT = {"cash": "1000", "insurance": "1110"}


def ensure_accounts(hospital_id):
    existing = set(LedgerAccount.objects.filter(hospital_id=hospital_id).values_list("code", flat=True))
    LedgerAccount.objects.bulk_create([
        LedgerAccount(hospital_id=hospital_id, code=c, name=n, group=g, tally_group=t, is_system=True)
        for c, n, g, t in DEFAULT_ACCOUNTS if c not in existing
    ])
    return {a.code: a for a in LedgerAccount.objects.filter(hospital_id=hospital_id)}


@transaction.atomic
def post(hospital_id, *, voucher_type, voucher_number, source_type, source_id, lines, narration="", party="", entry_date=None):
    """lines: [(account_code, debit, credit)] — must balance."""
    debit = sum(Decimal(str(d)) for _, d, _ in lines)
    credit = sum(Decimal(str(c)) for _, _, c in lines)
    if (debit - credit).copy_abs() > Decimal("0.01"):
        raise ValueError(f"Unbalanced voucher {voucher_number}: Dr {debit} ≠ Cr {credit}")
    accounts = ensure_accounts(hospital_id)
    entry, created = JournalEntry.objects.get_or_create(
        hospital_id=hospital_id, source_type=source_type, source_id=str(source_id), voucher_type=voucher_type,
        defaults={"voucher_number": voucher_number, "narration": narration[:255], "party_name": party[:200], "entry_date": entry_date or timezone.localdate()},
    )
    if not created:
        entry.lines.all().delete()
        entry.voucher_number, entry.narration, entry.party_name = voucher_number, narration[:255], party[:200]
        entry.save()
    JournalLine.objects.bulk_create([
        JournalLine(entry=entry, account=accounts[code], debit=Decimal(str(d)), credit=Decimal(str(c)))
        for code, d, c in lines if Decimal(str(d)) or Decimal(str(c))
    ])
    return entry


def post_bill(bill):
    if bill.is_interim or bill.status in ("draft", "cancelled"):
        return None
    items = list(bill.items.all())
    tax = sum((i.tax_amount for i in items), Decimal("0"))
    gross = sum((i.total_price for i in items), Decimal("0"))
    discount = Decimal(bill.discount_amount or 0)
    lines = [("1100", gross + tax - discount, 0), ("4000", 0, gross)]
    if tax:
        lines.append(("2100", 0, tax))
    if discount:
        lines.append(("4900", discount, 0))
    return post(bill.hospital_id, voucher_type="sales", voucher_number=bill.bill_number or f"B{bill.pk}", source_type="bill", source_id=bill.pk,
                lines=lines, narration=f"Bill {bill.bill_number} — {bill.patient.full_name}", party=bill.patient.full_name, entry_date=timezone.localdate(bill.created_at))


def post_payment(payment):
    acct = PAYMENT_ACCOUNT.get(payment.payment_method, "1010")
    return post(payment.hospital_id, voucher_type="receipt", voucher_number=f"RCPT{payment.pk}", source_type="payment", source_id=payment.pk,
                lines=[(acct, payment.amount, 0), ("1100", 0, payment.amount)],
                narration=f"Receipt against bill {payment.bill.bill_number} ({payment.payment_method})", party=payment.bill.patient.full_name,
                entry_date=timezone.localdate(payment.paid_at))


def post_vendor_invoice(inv):
    return post(inv.hospital_id, voucher_type="purchase", voucher_number=inv.invoice_number, source_type="vendor_invoice", source_id=inv.pk,
                lines=[("5000", inv.taxable_amount, 0), ("1300", inv.gst_amount, 0), ("2000", 0, inv.total_amount)],
                narration=f"Purchase invoice {inv.invoice_number}", party=inv.vendor.name, entry_date=inv.invoice_date)


def post_vendor_payment(pay):
    return post(pay.hospital_id, voucher_type="payment", voucher_number=f"VP{pay.pk}", source_type="vendor_payment", source_id=pay.pk,
                lines=[("2000", pay.amount + pay.tds_amount, 0), ("1000" if pay.mode == "cash" else "1010", 0, pay.amount), ("2110", 0, pay.tds_amount)],
                narration=f"Payment for {pay.invoice.invoice_number} ({pay.reference})", party=pay.invoice.vendor.name, entry_date=pay.paid_on)


def post_supplier_note(note):
    total = note.amount + note.gst_amount
    if note.kind == "debit":
        lines = [("2000", total, 0), ("5000", 0, note.amount), ("1300", 0, note.gst_amount)]
        vt = "debit_note"
    else:
        lines = [("2000", total, 0), ("5000", 0, note.amount), ("1300", 0, note.gst_amount)]
        vt = "credit_note"
    return post(note.hospital_id, voucher_type=vt, voucher_number=note.note_number, source_type="supplier_note", source_id=note.pk,
                lines=lines, narration=note.reason, party=note.vendor.name, entry_date=note.issued_on)


def trial_balance(hospital_id, start=None, end=None):
    qs = JournalLine.objects.filter(entry__hospital_id=hospital_id)
    if start:
        qs = qs.filter(entry__entry_date__gte=start)
    if end:
        qs = qs.filter(entry__entry_date__lte=end)
    rows = qs.values("account__code", "account__name", "account__group").annotate(dr=Sum("debit"), cr=Sum("credit")).order_by("account__code")
    out = [{"code": r["account__code"], "name": r["account__name"], "group": r["account__group"], "debit": float(r["dr"] or 0), "credit": float(r["cr"] or 0),
            "balance": float((r["dr"] or 0) - (r["cr"] or 0))} for r in rows]
    return {"accounts": out, "total_debit": round(sum(r["debit"] for r in out), 2), "total_credit": round(sum(r["credit"] for r in out), 2)}


# --- Tally --------------------------------------------------------------------------

TALLY_VOUCHER = {"sales": "Sales", "receipt": "Receipt", "purchase": "Purchase", "payment": "Payment", "journal": "Journal",
                 "debit_note": "Debit Note", "credit_note": "Credit Note"}


def tally_xml(entries, company_name):
    """Tally Prime 'Import Data → Vouchers' envelope. Ledger names are
    the account names; create the same ledgers in Tally (or import the
    masters first) once."""
    parts = [
        "<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA>",
        f"<REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME><STATICVARIABLES><SVCURRENTCOMPANY>{escape(company_name)}</SVCURRENTCOMPANY></STATICVARIABLES></REQUESTDESC><REQUESTDATA>",
    ]
    for e in entries:
        vt = TALLY_VOUCHER[e.voucher_type]
        parts.append(f'<TALLYMESSAGE xmlns:UDF="TallyUDF"><VOUCHER VCHTYPE="{vt}" ACTION="Create">')
        parts.append(f"<DATE>{e.entry_date:%Y%m%d}</DATE><VOUCHERTYPENAME>{vt}</VOUCHERTYPENAME><VOUCHERNUMBER>{escape(e.voucher_number)}</VOUCHERNUMBER>")
        parts.append(f"<PARTYLEDGERNAME>{escape(e.party_name)}</PARTYLEDGERNAME><NARRATION>{escape(e.narration)}</NARRATION>")
        for line in e.lines.select_related("account"):
            # Tally: debit amounts are negative with ISDEEMEDPOSITIVE=Yes.
            is_debit = line.debit > 0
            amount = -line.debit if is_debit else line.credit
            parts.append(
                f"<ALLLEDGERENTRIES.LIST><LEDGERNAME>{escape(line.account.name)}</LEDGERNAME>"
                f"<ISDEEMEDPOSITIVE>{'Yes' if is_debit else 'No'}</ISDEEMEDPOSITIVE><AMOUNT>{amount:.2f}</AMOUNT></ALLLEDGERENTRIES.LIST>"
            )
        parts.append("</VOUCHER></TALLYMESSAGE>")
    parts.append("</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>")
    return "".join(parts)


def tally_ledger_masters_xml(hospital_id, company_name):
    accounts = ensure_accounts(hospital_id).values()
    body = "".join(
        f'<TALLYMESSAGE xmlns:UDF="TallyUDF"><LEDGER NAME="{escape(a.name)}" ACTION="Create"><NAME>{escape(a.name)}</NAME>'
        f"<PARENT>{escape(a.tally_group or 'Suspense A/c')}</PARENT></LEDGER></TALLYMESSAGE>" for a in accounts
    )
    return ("<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>All Masters</REPORTNAME>"
            f"<STATICVARIABLES><SVCURRENTCOMPANY>{escape(company_name)}</SVCURRENTCOMPANY></STATICVARIABLES></REQUESTDESC><REQUESTDATA>{body}</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>")


# --- GST ------------------------------------------------------------------------------


def gst_outward_summary(hospital_id, start, end):
    """GSTR-1 style B2C summary (hospital bills are overwhelmingly B2C) +
    HSN/SAC-wise summary. Healthcare services (SAC 9993) are exempt; pharmacy
    goods and non-clinical services carry GST. Intra-state supply is split
    equally into CGST/SGST."""
    from apps.billing.models import BillItem

    items = BillItem.objects.filter(bill__hospital_id=hospital_id, bill__is_interim=False, bill__created_at__date__range=(start, end)).exclude(bill__status__in=["draft", "cancelled"])
    by_rate = defaultdict(lambda: {"taxable": Decimal("0"), "tax": Decimal("0"), "invoices": set()})
    by_hsn = defaultdict(lambda: {"description": "", "quantity": 0, "taxable": Decimal("0"), "tax": Decimal("0"), "rate": Decimal("0")})
    for i in items.select_related("bill"):
        r = by_rate[str(i.gst_rate)]
        r["taxable"] += i.total_price
        r["tax"] += i.tax_amount
        r["invoices"].add(i.bill_id)
        h = by_hsn[(i.hsn_sac or "9993", str(i.gst_rate))]
        h["description"] = h["description"] or i.description[:60]
        h["quantity"] += i.quantity
        h["taxable"] += i.total_price
        h["tax"] += i.tax_amount
        h["rate"] = i.gst_rate
    b2cs = [{"rate": float(k), "invoices": len(v["invoices"]), "taxable_value": float(v["taxable"]), "cgst": float(v["tax"] / 2), "sgst": float(v["tax"] / 2),
             "total_tax": float(v["tax"]), "type": "exempt" if Decimal(k) == 0 else "taxable"} for k, v in sorted(by_rate.items(), key=lambda kv: Decimal(kv[0]))]
    hsn = [{"hsn_sac": k[0], "description": v["description"], "rate": float(v["rate"]), "quantity": v["quantity"], "taxable_value": float(v["taxable"]),
            "cgst": float(v["tax"] / 2), "sgst": float(v["tax"] / 2)} for k, v in sorted(by_hsn.items())]
    return {"period": {"start": start, "end": end}, "b2cs": b2cs, "hsn": hsn,
            "totals": {"taxable_value": round(sum(r["taxable_value"] for r in b2cs), 2), "total_tax": round(sum(r["total_tax"] for r in b2cs), 2)}}


def gst_inward_summary(hospital_id, start, end):
    """Purchases (input tax credit register) — GSTR-2B reconciliation base."""
    from .models_fpm import VendorInvoice

    rows = VendorInvoice.objects.filter(hospital_id=hospital_id, invoice_date__range=(start, end)).select_related("vendor")
    return [{"vendor": r.vendor.name, "gstin": r.vendor.gstin, "invoice_number": r.invoice_number, "invoice_date": r.invoice_date,
             "taxable_value": float(r.taxable_amount), "gst": float(r.gst_amount), "total": float(r.total_amount)} for r in rows]
