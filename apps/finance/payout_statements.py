"""Consultant statements and the TDS report (revenue share, MedNet M2.22 parity).

- statement_pdf: the statement a consultant receives (services, gross,
  TDS, deduction heads, net).
- send_statement: emails the PDF and/or WhatsApps a summary with a signed
  download link (WhatsApp providers here send text only). Every attempt is
  logged as a PayoutStatementDelivery.
- tds_report: Sec 194J TDS deducted from consultants, as a summary by
  month, per consultant (with PAN, for 26Q / Form 16A) or per statement.
"""
import io
import logging
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.core import signing

from .models_payout import DoctorPayout, PayoutStatementDelivery

logger = logging.getLogger(__name__)

LINK_SALT = "finance.payout-statement"
LINK_MAX_AGE = 14 * 24 * 3600
SENDABLE = (DoctorPayout.Status.APPROVED, DoctorPayout.Status.PAID)
INLINE_SEND_LIMIT = 10  # more than this goes to Celery


def money(x):
    return f"Rs. {Decimal(x):,.2f}"


def statement_link(payout) -> str:
    token = signing.dumps({"p": payout.pk, "h": str(payout.hospital_id)}, salt=LINK_SALT, compress=True)
    return f"{settings.PUBLIC_APP_URL.rstrip('/')}/api/v1/finance/payout-statement/{token}/"


def payout_from_token(token):
    try:
        data = signing.loads(token, salt=LINK_SALT, max_age=LINK_MAX_AGE)
    except signing.BadSignature:
        return None
    return DoctorPayout.objects.filter(pk=data.get("p"), hospital_id=data.get("h"), status__in=SENDABLE).select_related("doctor", "hospital").first()


def statement_filename(payout):
    return f"Statement_{payout.doctor.name.replace(' ', '_')}_{payout.period_start}_to_{payout.period_end}.pdf"


def statement_pdf(payout) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small", fontSize=8, leading=10)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"Statement - Dr. {payout.doctor.name}")
    doctor = payout.doctor
    story = [
        Paragraph(payout.hospital.name, styles["Title"]),
        Paragraph("Consultant payout statement", styles["Heading2"]),
        Paragraph(f"Dr. {doctor.name}{f' &middot; PAN {doctor.pan}' if doctor.pan else ''}<br/>"
                  f"Period: {payout.period_start:%d %b %Y} to {payout.period_end:%d %b %Y} &middot; Status: {payout.get_status_display()}"
                  f"{f' &middot; Paid {payout.paid_on:%d %b %Y} ({payout.payment_mode.upper()} {payout.payment_reference})' if payout.paid_on else ''}",
                  styles["BodyText"]),
        Spacer(1, 6 * mm),
    ]
    rows = [["Date", "Bill", "Patient", "Service", "Net amount", "Medicine cost", "Payout"]]
    for ln in payout.lines.all():
        rows.append([f"{ln.service_date:%d-%m-%y}", ln.bill_number, Paragraph(ln.patient_name, small), Paragraph(ln.description, small),
                     f"{ln.base_amount:,.2f}", f"{ln.medicine_cost:,.2f}" if ln.medicine_cost else "", f"{ln.payout_amount:,.2f}"])
    table = Table(rows, colWidths=[17 * mm, 24 * mm, 35 * mm, 50 * mm, 22 * mm, 16 * mm, 20 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3864")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("ALIGN", (4, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
    ]))
    story += [table, Spacer(1, 6 * mm)]
    totals = [["Gross payout", money(payout.gross_payout)], [f"TDS u/s 194J @ {payout.tds_percent:g}%", f"- {money(payout.tds_amount)}"]]
    totals += [[f"{d.head.name}{f' ({d.remarks})' if d.remarks else ''}", f"- {money(d.amount)}"] for d in payout.deductions.select_related("head")]
    totals.append(["Net payable", money(payout.net_payable)])
    t2 = Table(totals, colWidths=[70 * mm, 40 * mm], hAlign="RIGHT")
    t2.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("LINEABOVE", (0, -1), (-1, -1), 0.5, colors.black)]))
    story += [t2, Spacer(1, 8 * mm), Paragraph("This is a computer-generated statement. Please contact the accounts department for any query.", small)]
    doc.build(story)
    return buf.getvalue()


def summary_text(payout) -> str:
    return (f"Dear Dr. {payout.doctor.name}, your payout statement from {payout.hospital.name} for "
            f"{payout.period_start:%d %b} to {payout.period_end:%d %b %Y}: gross {money(payout.gross_payout)}, "
            f"TDS {money(payout.tds_amount)}"
            f"{f', deductions {money(payout.deductions_total)}' if payout.deductions_total else ''}, net {money(payout.net_payable)}.")


def send_statement(payout, channels, user=None):
    """Returns the PayoutStatementDelivery rows created. Never raises."""
    from django.core.mail import EmailMessage

    from apps.communications.adapters import get_whatsapp_provider

    out = []
    doctor = payout.doctor

    def log(channel, recipient, error=""):
        out.append(PayoutStatementDelivery.objects.create(hospital_id=payout.hospital_id, payout=payout, channel=channel,
                                                          recipient=recipient, sent=not error, error=error[:255], sent_by=user))

    if payout.status not in SENDABLE:
        log(channels[0] if channels else "email", "", "Approve the statement before sending it.")
        return out
    text = summary_text(payout)
    if "email" in channels:
        if not doctor.email:
            log("email", "", "No email address on the doctor's profile.")
        else:
            try:
                msg = EmailMessage(subject=f"Payout statement {payout.period_start:%d %b} to {payout.period_end:%d %b %Y} - {payout.hospital.name}",
                                   body=text + "\n\nThe statement is attached.", from_email=settings.MESSAGING_FROM_EMAIL, to=[doctor.email])
                msg.attach(statement_filename(payout), statement_pdf(payout), "application/pdf")
                msg.send(fail_silently=False)
                log("email", doctor.email)
            except Exception as exc:  # noqa: BLE001 - recorded for staff to retry
                logger.exception("Statement email failed for payout %s", payout.pk)
                log("email", doctor.email, f"Email failed: {exc}")
    if "whatsapp" in channels:
        if not doctor.phone:
            log("whatsapp", "", "No phone number on the doctor's profile.")
        else:
            try:
                get_whatsapp_provider().send(to=doctor.phone, body=f"{text}\nDownload: {statement_link(payout)}")
                log("whatsapp", doctor.phone)
            except Exception as exc:  # noqa: BLE001
                log("whatsapp", doctor.phone, f"WhatsApp failed: {exc}")
    return out


def send_many(payout_ids, channels, user_id=None):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(pk=user_id).first() if user_id else None
    sent = failed = 0
    for payout in DoctorPayout.objects.filter(pk__in=payout_ids).select_related("doctor", "hospital"):
        for d in send_statement(payout, channels, user):
            sent, failed = (sent + 1, failed) if d.sent else (sent, failed + 1)
    return {"sent": sent, "failed": failed}


# --- TDS report ------------------------------------------------------------

def fy_quarter(d):
    """Indian financial-year quarter, e.g. 'FY2026-27 Q1' for May 2026."""
    start = d.year if d.month >= 4 else d.year - 1
    q = (d.month - 4) % 12 // 3 + 1
    return f"FY{start}-{str(start + 1)[-2:]} Q{q}"


def tds_statements(hospital_id, start, end, *, basis="paid", doctor=None):
    """basis=paid: paid statements by payment date (when TDS is deducted).
    basis=period: approved or paid statements by period end (accrual)."""
    qs = DoctorPayout.objects.filter(hospital_id=hospital_id).select_related("doctor")
    if basis == "period":
        qs = qs.filter(status__in=SENDABLE, period_end__range=(start, end))
    else:
        qs = qs.filter(status=DoctorPayout.Status.PAID, paid_on__range=(start, end))
    if doctor:
        qs = qs.filter(doctor=doctor)
    return list(qs.order_by("paid_on", "period_end", "doctor__name"))


def _date(p, basis):
    return p.period_end if basis == "period" else p.paid_on


def tds_report(statements, view, basis="paid"):
    """Returns (columns, rows, totals) for view = summary | consultant | detail."""
    zero = Decimal("0")
    keys = ("gross_payout", "tds_amount", "deductions_total", "net_payable")
    totals = {k: sum((getattr(p, k) for p in statements), zero) for k in keys}
    totals["statements"] = len(statements)
    if view == "detail":
        cols = ["Date", "Quarter", "Consultant", "PAN", "Period", "Reference", "Gross", "TDS %", "TDS", "Deductions", "Net paid"]
        rows = [[str(_date(p, basis)), fy_quarter(_date(p, basis)), f"Dr. {p.doctor.name}", p.doctor.pan or "", f"{p.period_start} to {p.period_end}",
                 p.payment_reference, p.gross_payout, p.tds_percent, p.tds_amount, p.deductions_total, p.net_payable] for p in statements]
        return cols, rows, totals
    if view == "consultant":
        groups = defaultdict(list)
        for p in statements:
            groups[p.doctor].append(p)
        cols = ["Consultant", "PAN", "Statements", "Gross", "TDS", "Deductions", "Net paid"]
        rows = [[f"Dr. {d.name}", d.pan or "", len(ps)] + [sum((getattr(p, k) for p in ps), zero) for k in keys]
                for d, ps in sorted(groups.items(), key=lambda kv: kv[0].name)]
        return cols, rows, totals
    groups = defaultdict(list)
    for p in statements:
        groups[_date(p, basis).strftime("%Y-%m")].append(p)
    cols = ["Month", "Quarter", "Consultants", "Statements", "Gross", "TDS", "Deductions", "Net paid"]
    rows = []
    for month, ps in sorted(groups.items()):
        d = _date(ps[0], basis)
        rows.append([d.strftime("%b %Y"), fy_quarter(d), len({p.doctor_id for p in ps}), len(ps)] + [sum((getattr(p, k) for p in ps), zero) for k in keys])
    return cols, rows, totals
