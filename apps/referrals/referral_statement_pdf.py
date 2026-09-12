from decimal import Decimal
from io import BytesIO
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet


def _money(val) -> str:
    try:
        amt = Decimal(str(val))
        return f"Rs. {amt:,.2f}"
    except Exception:
        return f"Rs. {val}"


def render_referral_statement_pdf(doctor, referral_records) -> bytes:
    """Renders a Referring Doctor monthly commission and case attribution statement."""
    hospital = doctor.hospital
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
    )

    styles = getSampleStyleSheet()

    hosp_name_style = ParagraphStyle(
        "HospName",
        parent=styles["Heading1"],
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#0f766e"),  # Teal-700
        alignment=1,
    )
    hosp_sub_style = ParagraphStyle(
        "HospSub",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#475569"),
        alignment=1,
    )
    title_style = ParagraphStyle(
        "StatementTitle",
        parent=styles["Heading2"],
        fontSize=12.5,
        leading=16,
        textColor=colors.HexColor("#1e293b"),
        alignment=1,
        spaceAfter=3,
    )
    section_style = ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading3"],
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor("#0f766e"),
        spaceBefore=5,
        spaceAfter=3,
    )
    label_style = ParagraphStyle(
        "FieldLabel",
        parent=styles["Normal"],
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#64748b"),
        fontName="Helvetica-Bold",
    )
    val_style = ParagraphStyle(
        "FieldValue",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#0f172a"),
    )
    bold_val_style = ParagraphStyle(
        "BoldFieldValue",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#0f172a"),
        fontName="Helvetica-Bold",
    )
    disclaimer_style = ParagraphStyle(
        "Disclaimer",
        parent=styles["Normal"],
        fontSize=7.5,
        leading=10.5,
        textColor=colors.HexColor("#64748b"),
    )

    story = []

    # 1. Hospital Header
    hosp_name = getattr(hospital, "name", "Hospital Care Center")
    story.append(Paragraph(hosp_name.upper(), hosp_name_style))
    contact_parts = []
    if getattr(hospital, "city", None):
        contact_parts.append(hospital.city)
    if getattr(hospital, "phone_number", None):
        contact_parts.append(f"Phone: {hospital.phone_number}")
    if getattr(hospital, "email", None):
        contact_parts.append(f"Email: {hospital.email}")
    contact_str = "  |  ".join(contact_parts) or "Doctor Liaison & Business Development Division"
    story.append(Paragraph(contact_str, hosp_sub_style))
    story.append(Spacer(1, 3 * mm))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#0f766e"), spaceAfter=6))

    # 2. Statement Title
    statement_date = timezone.now().strftime("%d %b %Y")
    statement_ref = f"REF-STMT-{doctor.pk:04d}-{timezone.now().strftime('%Y%m')}"

    story.append(Paragraph("REFERRAL COMMISSION & CASE ATTRIBUTION STATEMENT", title_style))
    story.append(Spacer(1, 1.5 * mm))

    # 3. Doctor Details Grid
    doc_grid = [
        [
            Paragraph("Referring Doctor", label_style),
            Paragraph(f"<b>{doctor.name}</b>", bold_val_style),
            Paragraph("Statement Ref", label_style),
            Paragraph(statement_ref, bold_val_style),
        ],
        [
            Paragraph("Clinic / Hospital", label_style),
            Paragraph(doctor.clinic_name or "Private Practice", val_style),
            Paragraph("Date Issued", label_style),
            Paragraph(statement_date, val_style),
        ],
        [
            Paragraph("Speciality", label_style),
            Paragraph(doctor.speciality or "General Practice", val_style),
            Paragraph("Partnership Tier", label_style),
            Paragraph(f"<b>{doctor.get_tier_display()}</b>", bold_val_style),
        ],
        [
            Paragraph("Mobile / City", label_style),
            Paragraph(f"{doctor.mobile} ({doctor.city})", val_style),
            Paragraph("Active Status", label_style),
            Paragraph("Active Partner" if doctor.is_active else "Inactive", val_style),
        ],
    ]
    d_table = Table(doc_grid, colWidths=[35 * mm, 55 * mm, 40 * mm, 50 * mm])
    d_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#f1f5f9")),
                ("PADDING", (0, 0), (-1, -1), 3.5),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f8fafc")),
                ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f8fafc")),
            ]
        )
    )
    story.append(d_table)
    story.append(Spacer(1, 4 * mm))

    # 4. Itemized Case Attribution Table
    story.append(Paragraph("PATIENT REFERRAL TRANSACTIONS & ATTRIBUTED REVENUE", section_style))

    table_data = [
        ["Sr.", "Date", "Patient Name", "Department", "Revenue (INR)", "Comm %", "Payable (INR)", "Status"]
    ]

    total_revenue = Decimal("0.00")
    total_commission = Decimal("0.00")

    for idx, ref in enumerate(referral_records, start=1):
        rev = Decimal(str(ref.attributed_revenue or 0))
        pct = Decimal(str(ref.commission_percentage or 10))
        comm = (rev * pct / Decimal("100.00")).quantize(Decimal("0.01"))
        total_revenue += rev
        total_commission += comm

        p_name = ref.patient.full_name if ref.patient else "Patient"
        dept_name = ref.department.name if ref.department else "General"
        ref_date = ref.referred_at.strftime("%d/%m/%Y") if ref.referred_at else "-"
        status_label = ref.get_status_display()

        table_data.append([
            str(idx),
            ref_date,
            p_name,
            dept_name,
            f"{rev:,.2f}",
            f"{pct}%",
            f"{comm:,.2f}",
            status_label,
        ])

    if len(table_data) == 1:
        table_data.append(["-", "-", "No referrals recorded for this period", "-", "0.00", "0%", "0.00", "-"])

    # Summary Total Row
    table_data.append([
        "",
        "",
        "TOTAL ATTRIBUTED SUMMARY",
        "",
        f"Rs. {total_revenue:,.2f}",
        "",
        f"Rs. {total_commission:,.2f}",
        "",
    ])

    r_table = Table(table_data, colWidths=[10 * mm, 20 * mm, 45 * mm, 30 * mm, 25 * mm, 15 * mm, 23 * mm, 12 * mm])
    r_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("ALIGN", (4, 0), (6, -1), "RIGHT"),
                ("FONTSIZE", (0, 1), (-1, -1), 7.5),
                ("PADDING", (0, 0), (-1, -1), 3.5),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("INNERGRID", (0, 0), (-1, -2), 0.5, colors.HexColor("#e2e8f0")),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f0fdfa")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, -1), (-1, -1), colors.HexColor("#0f766e")),
                ("LINEABOVE", (0, -1), (-1, -1), 1.5, colors.HexColor("#0f766e")),
            ]
        )
    )
    story.append(r_table)
    story.append(Spacer(1, 4 * mm))

    # 5. KPI Summary Box
    summary_box = [
        [
            Paragraph("Total Cases Referred", label_style),
            Paragraph(f"<b>{len(referral_records)}</b>", bold_val_style),
            Paragraph("Total Attributed Billing", label_style),
            Paragraph(f"<b>{_money(total_revenue)}</b>", bold_val_style),
            Paragraph("Net Commission Payable", label_style),
            Paragraph(f"<b><font color='#0f766e'>{_money(total_commission)}</font></b>", bold_val_style),
        ]
    ]
    sum_table = Table(summary_box, colWidths=[30 * mm, 25 * mm, 35 * mm, 30 * mm, 32 * mm, 28 * mm])
    sum_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("PADDING", (0, 0), (-1, -1), 4),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(sum_table)
    story.append(Spacer(1, 5 * mm))

    # 6. Terms & Disclaimer
    statement_note = (
        "<b>Confidential Statement:</b> This statement is prepared strictly for internal professional settlement between the hospital "
        "and the consulting referring doctor. All revenue figures reflect final settled patient bills. Payments are processed via "
        "direct bank transfer subject to applicable TDS deductions under Section 194J of the Income Tax Act."
    )
    story.append(Paragraph(statement_note, disclaimer_style))
    story.append(Spacer(1, 10 * mm))

    # 7. Signatures
    sig_data = [
        [
            Paragraph("____________________________________<br/><b>Doctor Liaison Officer (PRO)</b><br/>Hospital Business Development Desk", disclaimer_style),
            Paragraph("____________________________________<br/><b>Finance & Accounts Manager</b><br/>Hospital Billing & Audit Desk", disclaimer_style),
            Paragraph("____________________________________<br/><b>Medical Director / COO</b><br/>Executive Administration", disclaimer_style),
        ]
    ]
    sig_table = Table(sig_data, colWidths=[60 * mm, 60 * mm, 60 * mm])
    sig_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("PADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(sig_table)

    doc.build(story)
    return buffer.getvalue()
