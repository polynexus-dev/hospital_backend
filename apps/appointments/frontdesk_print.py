"""Printouts for the front desk: registration card (credit-card size),
UHID sticker (50 x 25 mm) and the OPD receipt (A5)."""
import io

from django.utils import timezone
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import code128, qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


def _age(patient):
    if not patient.date_of_birth:
        return ""
    today = timezone.localdate()
    dob = patient.date_of_birth
    years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return f"{'~' if patient.dob_is_estimated else ''}{years} Y"


def _sex(patient):
    return (patient.gender or "")[:1].upper()


def _masked(mobile):
    digits = "".join(ch for ch in str(mobile or "") if ch.isdigit())
    return f"XXXXXX{digits[-4:]}" if len(digits) >= 4 else ""


def _qr(value, size):
    widget = qr.QrCodeWidget(value)
    x1, y1, x2, y2 = widget.getBounds()
    drawing = Drawing(size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0])
    drawing.add(widget)
    return drawing


def registration_card(patient) -> bytes:
    """85.6 x 54 mm card: hospital, name, UHID barcode and QR, age/sex, masked mobile."""
    w, h = 85.6 * mm, 54 * mm
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(w, h))
    c.setFillColorRGB(0.07, 0.21, 0.36)  # navy band
    c.rect(0, h - 11 * mm, w, 11 * mm, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(4 * mm, h - 7 * mm, (patient.hospital.name or "")[:42])
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(4 * mm, h - 17 * mm, f"{patient.title + ' ' if patient.title else ''}{patient.full_name}"[:34])
    c.setFont("Helvetica", 7.5)
    c.drawString(4 * mm, h - 22 * mm, f"UHID: {patient.uhid}    {_age(patient)} / {_sex(patient)}")
    c.drawString(4 * mm, h - 26 * mm, f"Mobile: {_masked(patient.mobile)}    Registered: {timezone.localtime(patient.created_at):%d %b %Y}")
    barcode = code128.Code128(patient.uhid or str(patient.pk), barHeight=10 * mm, barWidth=0.32 * mm)
    barcode.drawOn(c, 2 * mm, 4 * mm)
    renderPDF.draw(_qr(patient.uhid or str(patient.pk), 20 * mm), c, w - 23 * mm, 3 * mm)
    c.setFont("Helvetica", 5.5)
    c.drawString(4 * mm, 1.5 * mm, "Carry this card on every visit. If found, please return to the hospital.")
    c.showPage()
    c.save()
    return buf.getvalue()


def uhid_sticker(patient, copies=1) -> bytes:
    """50 x 25 mm sticker for files and sample tubes."""
    w, h = 50 * mm, 25 * mm
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(w, h))
    for _ in range(max(1, min(int(copies), 20))):
        c.setFont("Helvetica-Bold", 7)
        c.drawString(2 * mm, h - 4 * mm, patient.full_name[:28])
        c.setFont("Helvetica", 6)
        c.drawString(2 * mm, h - 7 * mm, f"{patient.uhid}   {_age(patient)} / {_sex(patient)}")
        code128.Code128(patient.uhid or str(patient.pk), barHeight=11 * mm, barWidth=0.25 * mm).drawOn(c, 1 * mm, 3 * mm)
        c.showPage()
    c.save()
    return buf.getvalue()


def opd_receipt(visit) -> bytes:
    """A5 receipt: visit, token, doctor, items, discount, rounding, payments."""
    from decimal import Decimal

    bill, patient = visit.bill, visit.patient
    w, h = A5
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A5)
    y = h - 15 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(12 * mm, y, (visit.hospital.name or "")[:50])
    y -= 6 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(12 * mm, y, "OPD Receipt" if bill and bill.status == "paid" else "OPD Bill")
    c.setFont("Helvetica", 8)
    c.drawRightString(w - 12 * mm, y, f"{bill.bill_number if bill else ''}   {timezone.localtime(visit.created_at):%d %b %Y %H:%M}")
    y -= 8 * mm
    rows = [
        ("Patient", f"{patient.title + ' ' if patient.title else ''}{patient.full_name}  ({_age(patient)} / {_sex(patient)})"),
        ("UHID", patient.uhid), ("Visit", f"{visit.visit_number}   {visit.get_visit_type_display()}"),
        ("Doctor", f"Dr. {visit.doctor.name}" + (f"  ({visit.department.name})" if visit.department_id else "")),
        ("Token", str(visit.appointment.queue_token or "")), ("Payer", visit.get_payer_type_display()),
    ]
    for label, value in rows:
        c.setFont("Helvetica-Bold", 8)
        c.drawString(12 * mm, y, label)
        c.setFont("Helvetica", 8)
        c.drawString(35 * mm, y, str(value)[:70])
        y -= 5 * mm
    y -= 2 * mm
    c.line(12 * mm, y, w - 12 * mm, y)
    y -= 5 * mm
    if bill:
        for item in bill.items.all():
            c.drawString(12 * mm, y, item.description[:60])
            c.drawRightString(w - 12 * mm, y, f"{item.total_price:,.2f}")
            y -= 5 * mm
        y -= 1 * mm
        c.line(12 * mm, y, w - 12 * mm, y)
        y -= 5 * mm
        totals = [("Total", bill.total_amount + bill.tax_amount)]
        if bill.discount_amount:
            totals.append((f"Discount ({bill.discount_reason[:30]})", -bill.discount_amount))
        if bill.round_off:
            totals.append(("Rounded down", -bill.round_off))
        totals.append(("Net payable", bill.net_amount))
        for label, value in totals:
            c.setFont("Helvetica-Bold" if label == "Net payable" else "Helvetica", 8.5)
            c.drawString(70 * mm, y, label)
            c.drawRightString(w - 12 * mm, y, f"{Decimal(value):,.2f}")
            y -= 5 * mm
        y -= 2 * mm
        c.setFont("Helvetica", 8)
        for p in bill.payments.all():
            c.drawString(12 * mm, y, f"Paid {p.get_payment_method_display()}{'  ref ' + p.transaction_id if p.transaction_id else ''}")
            c.drawRightString(w - 12 * mm, y, f"{p.amount:,.2f}")
            y -= 5 * mm
        if bill.status != "paid":
            c.setFont("Helvetica-Bold", 8)
            c.drawString(12 * mm, y, "Billed on credit / payment pending" if bill.status != "draft" else "Discount awaiting approval")
            y -= 5 * mm
    c.setFont("Helvetica", 7)
    c.drawString(12 * mm, 12 * mm, f"Registered by {visit.registered_by.get_full_name() or visit.registered_by.email if visit.registered_by else ''}")
    c.drawString(12 * mm, 8 * mm, "This is a computer-generated receipt.")
    c.showPage()
    c.save()
    return buf.getvalue()
