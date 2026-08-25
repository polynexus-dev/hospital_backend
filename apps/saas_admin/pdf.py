"""Renders a downloadable PDF for a TenantInvoice — apps.saas_admin.views.
TenantInvoiceViewSet.download. Deliberately labelled "Invoice" rather
than "Tax Invoice": it has no GSTIN, tax breakdown, or HSN/SAC code,
none of which this app has data for (the platform's own registered
GSTIN/address/bank details, and each hospital's GSTIN, aren't captured
anywhere in this schema). If a GST-compliant tax invoice is ever needed,
that data has to be collected first — this renders a billing statement
with what the model actually has: who's being billed, for what period,
how much, and whether it's paid."""

from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

STATUS_COLORS = {
    "paid": colors.HexColor("#15803d"),
    "unpaid": colors.HexColor("#b45309"),
    "overdue": colors.HexColor("#b91c1c"),
}


def _money(amount: Decimal) -> str:
    return f"Rs. {amount:,.2f}"


def render_invoice_pdf(invoice) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("InvoiceTitle", parent=styles["Heading1"], fontSize=20, spaceAfter=2)
    subtitle_style = ParagraphStyle("InvoiceSubtitle", parent=styles["Normal"], textColor=colors.HexColor("#6b7280"))
    section_style = ParagraphStyle("Section", parent=styles["Heading3"], spaceBefore=10, spaceAfter=4)
    status_style = ParagraphStyle(
        "Status", parent=styles["Normal"], fontSize=12, textColor=STATUS_COLORS.get(invoice.status, colors.black),
        alignment=2,  # right
    )

    elements = [
        Paragraph("Polynexus Hospital CRM", title_style),
        Paragraph("SaaS Platform — Billing Invoice", subtitle_style),
        Spacer(1, 10 * mm),
        Paragraph(f"INVOICE {invoice.invoice_number}", section_style),
        Paragraph(invoice.get_status_display().upper(), status_style),
        Spacer(1, 4 * mm),
    ]

    meta_table = Table(
        [
            ["Billed to", invoice.hospital.name],
            ["Hospital location", f"{invoice.hospital.city}, {invoice.hospital.state}".strip(", ")],
            ["Billing period", f"{invoice.billing_period_start:%d %b %Y} – {invoice.billing_period_end:%d %b %Y}"],
            ["Due date", f"{invoice.due_date:%d %b %Y}"],
            ["Issued", f"{invoice.created_at:%d %b %Y}"],
        ] + ([["Paid on", f"{invoice.paid_at:%d %b %Y}"]] if invoice.paid_at else []),
        colWidths=[45 * mm, 120 * mm],
    )
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#6b7280")),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 8 * mm))

    tier_label = invoice.subscription.get_tier_display() if invoice.subscription_id else "Hospital Management"
    line_items = Table(
        [
            ["Description", "Amount"],
            [f"{tier_label} SaaS subscription — {invoice.billing_period_start:%b %Y}", _money(invoice.amount)],
            ["Total due", _money(invoice.amount)],
        ],
        colWidths=[130 * mm, 35 * mm],
    )
    line_items.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.75, colors.HexColor("#d1d5db")),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
    ]))
    elements.append(line_items)

    if invoice.notes:
        elements.append(Spacer(1, 6 * mm))
        elements.append(Paragraph("Notes", section_style))
        elements.append(Paragraph(invoice.notes, styles["Normal"]))

    elements.append(Spacer(1, 12 * mm))
    elements.append(Paragraph(
        "This is a billing statement for platform subscription services, not a GST tax invoice.",
        ParagraphStyle("Footer", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#9ca3af")),
    ))

    doc.build(elements)
    return buffer.getvalue()
