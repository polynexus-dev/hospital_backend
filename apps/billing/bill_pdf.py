from decimal import Decimal
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet


STATUS_COLORS = {
    "paid": colors.HexColor("#15803d"),
    "unpaid": colors.HexColor("#b91c1c"),
    "partially_paid": colors.HexColor("#b45309"),
    "draft": colors.HexColor("#4b5563"),
    "cancelled": colors.HexColor("#9ca3af"),
}


def _money(val) -> str:
    try:
        amt = Decimal(str(val))
        return f"Rs. {amt:,.2f}"
    except Exception:
        return f"Rs. {val}"


def render_bill_pdf(bill) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )

    styles = getSampleStyleSheet()
    hospital = bill.hospital
    patient = bill.patient

    hosp_name_style = ParagraphStyle(
        "HospName",
        parent=styles["Heading1"],
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#1e3a8a"),  # Deep Navy Blue
        alignment=1,
    )
    hosp_sub_style = ParagraphStyle(
        "HospSub",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#4b5563"),
        alignment=1,
    )
    title_style = ParagraphStyle(
        "BillTitle",
        parent=styles["Heading2"],
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#1e293b"),
        alignment=1,
    )
    section_head_style = ParagraphStyle(
        "SectionHead",
        parent=styles["Heading4"],
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#1e3a8a"),
        spaceBefore=6,
        spaceAfter=3,
    )
    cell_bold = ParagraphStyle("CellBold", parent=styles["Normal"], fontSize=9, leading=12, fontName="Helvetica-Bold")
    cell_norm = ParagraphStyle("CellNorm", parent=styles["Normal"], fontSize=9, leading=12)
    cell_right = ParagraphStyle("CellRight", parent=styles["Normal"], fontSize=9, leading=12, alignment=2)
    cell_right_bold = ParagraphStyle("CellRightBold", parent=styles["Normal"], fontSize=9, leading=12, fontName="Helvetica-Bold", alignment=2)

    status_color = STATUS_COLORS.get(bill.status, colors.black)
    status_label = bill.get_status_display().upper() if hasattr(bill, "get_status_display") else str(bill.status).upper()

    elements = []

    # 1. Hospital Header
    hosp_name = getattr(hospital, "name", "Hospital Management System")
    hosp_location = f"{getattr(hospital, 'address', '')} {getattr(hospital, 'city', '')} {getattr(hospital, 'state', '')}".strip()
    elements.append(Paragraph(hosp_name.upper(), hosp_name_style))
    if hosp_location:
        elements.append(Paragraph(hosp_location, hosp_sub_style))
    elements.append(Spacer(1, 3 * mm))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#1e3a8a"), spaceBefore=2, spaceAfter=6))

    # 2. Document Title & Status
    elements.append(Paragraph("PATIENT HOSPITAL INVOICE & RECEIPT", title_style))
    elements.append(Spacer(1, 3 * mm))

    # 3. Patient & Billing Meta Table
    pt_name = getattr(patient, "full_name", f"{getattr(patient, 'first_name', '')} {getattr(patient, 'last_name', '')}".strip())
    uhid = getattr(patient, "uhid", "N/A")
    bill_date = bill.created_at.strftime("%d-%b-%Y %I:%M %p") if bill.created_at else "N/A"
    admission_info = f"IPD Admission #{bill.admission_id}" if bill.admission_id else "OPD / Daycare Consultation"

    meta_data = [
        [
            Paragraph("<b>Patient Name:</b>", cell_norm),
            Paragraph(pt_name, cell_bold),
            Paragraph("<b>Invoice No:</b>", cell_norm),
            Paragraph(f"<b>BILL-{bill.id:05d}</b>", cell_bold),
        ],
        [
            Paragraph("<b>UHID:</b>", cell_norm),
            Paragraph(f"<font color='#1e3a8a'><b>{uhid}</b></font>", cell_bold),
            Paragraph("<b>Billing Date:</b>", cell_norm),
            Paragraph(bill_date, cell_norm),
        ],
        [
            Paragraph("<b>Encounter / Admission:</b>", cell_norm),
            Paragraph(admission_info, cell_norm),
            Paragraph("<b>Payment Status:</b>", cell_norm),
            Paragraph(f"<b><font color='{status_color.hexval()}'>{status_label}</font></b>", cell_bold),
        ],
    ]

    meta_table = Table(meta_data, colWidths=[35 * mm, 55 * mm, 35 * mm, 49 * mm])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#cbd5e1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 6 * mm))

    # 4. Itemized Charges Table
    elements.append(Paragraph("Itemized Billable Services & Charges", section_head_style))

    items = list(bill.items.all())
    item_rows = [
        [
            Paragraph("<b>#</b>", cell_bold),
            Paragraph("<b>Particulars / Service Description</b>", cell_bold),
            Paragraph("<b>Qty</b>", cell_right_bold),
            Paragraph("<b>Unit Price</b>", cell_right_bold),
            Paragraph("<b>Amount (Rs.)</b>", cell_right_bold),
        ]
    ]

    if items:
        for idx, itm in enumerate(items, start=1):
            item_rows.append([
                Paragraph(str(idx), cell_norm),
                Paragraph(itm.description, cell_norm),
                Paragraph(str(itm.quantity), cell_right),
                Paragraph(_money(itm.unit_price), cell_right),
                Paragraph(_money(itm.total_price), cell_right_bold),
            ])
    else:
        # Fallback single line item if no itemized breakdown
        item_rows.append([
            Paragraph("1", cell_norm),
            Paragraph("Hospital Medical Consultation & Care Services", cell_norm),
            Paragraph("1", cell_right),
            Paragraph(_money(bill.total_amount), cell_right),
            Paragraph(_money(bill.total_amount), cell_right_bold),
        ])

    items_table = Table(item_rows, colWidths=[10 * mm, 80 * mm, 18 * mm, 32 * mm, 34 * mm])
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#cbd5e1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(items_table)
    elements.append(Spacer(1, 4 * mm))

    # 5. Financial Summary Box (Right Aligned Table)
    payments = list(bill.payments.all())
    total_paid = sum(p.amount for p in payments) if payments else Decimal(0)
    balance_due = Decimal(str(bill.net_amount)) - Decimal(str(total_paid))
    if balance_due < Decimal(0):
        balance_due = Decimal(0)

    summary_rows = [
        [Paragraph("<b>Gross Total:</b>", cell_right), Paragraph(_money(bill.total_amount), cell_right)],
        [Paragraph("<b>Discount / Concession:</b>", cell_right), Paragraph(_money(bill.discount_amount), cell_right)],
        [Paragraph("<b>Net Bill Payable:</b>", cell_right_bold), Paragraph(f"<b>{_money(bill.net_amount)}</b>", cell_right_bold)],
        [Paragraph("<b>Total Payments Received:</b>", cell_right), Paragraph(_money(total_paid), cell_right)],
        [Paragraph("<b>Balance Outstanding:</b>", cell_right_bold), Paragraph(f"<font color='#b91c1c'><b>{_money(balance_due)}</b></font>", cell_right_bold)],
    ]

    summary_table = Table(summary_rows, colWidths=[120 * mm, 54 * mm])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#f8fafc")),
        ("LINEABOVE", (0, 2), (-1, 2), 1, colors.HexColor("#cbd5e1")),
        ("LINEBELOW", (0, 2), (-1, 2), 1, colors.HexColor("#cbd5e1")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    elements.append(summary_table)

    # 6. Payment Receipts Log (if any)
    if payments:
        elements.append(Spacer(1, 4 * mm))
        elements.append(Paragraph("Receipts / Transactions Collected", section_head_style))
        pay_rows = [
            [
                Paragraph("<b>Date & Time</b>", cell_bold),
                Paragraph("<b>Payment Mode</b>", cell_bold),
                Paragraph("<b>Transaction Ref</b>", cell_bold),
                Paragraph("<b>Amount Paid</b>", cell_right_bold),
            ]
        ]
        for p in payments:
            pay_rows.append([
                Paragraph(p.paid_at.strftime("%d-%b-%Y %I:%M %p") if p.paid_at else "-", cell_norm),
                Paragraph(p.get_payment_method_display().upper() if hasattr(p, "get_payment_method_display") else str(p.payment_method).upper(), cell_norm),
                Paragraph(p.transaction_id or "Direct", cell_norm),
                Paragraph(_money(p.amount), cell_right_bold),
            ])
        pay_table = Table(pay_rows, colWidths=[44 * mm, 35 * mm, 55 * mm, 40 * mm])
        pay_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements.append(pay_table)

    # 7. Terms & Authorized Signature
    elements.append(Spacer(1, 10 * mm))
    sig_data = [
        [
            Paragraph("<font color='#64748b' size=7>Thank you for choosing us for your medical care.<br/>Bills once issued are subject to hospital audit. Disputes must be reported within 7 days.</font>", cell_norm),
            Paragraph(f"<b>For {hosp_name}</b><br/><br/><font color='#64748b'>Authorized Billing Officer / Cashier</font>", ParagraphStyle("CashierSig", parent=cell_norm, alignment=2)),
        ]
    ]
    sig_table = Table(sig_data, colWidths=[110 * mm, 64 * mm])
    sig_table.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    elements.append(sig_table)

    doc.build(elements)
    return buffer.getvalue()
