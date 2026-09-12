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


def render_mis_pdf(hospital, summary: dict, dept_doctor_rows: list, revenue_rows: list, start_date: str, end_date: str, period_label: str = "Executive") -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
    )

    styles = getSampleStyleSheet()

    hosp_name_style = ParagraphStyle(
        "HospName",
        parent=styles["Heading1"],
        fontSize=17,
        leading=21,
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
        "MISTitle",
        parent=styles["Heading2"],
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#1e293b"),
        alignment=1,
        spaceAfter=3,
    )
    section_style = ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading3"],
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#0f766e"),
        spaceBefore=5,
        spaceAfter=4,
    )
    table_cell = ParagraphStyle(
        "TableCell",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#1e293b"),
    )
    table_cell_bold = ParagraphStyle(
        "TableCellBold",
        parent=table_cell,
        fontName="Helvetica-Bold",
    )
    table_cell_right = ParagraphStyle(
        "TableCellRight",
        parent=table_cell,
        alignment=2,
    )
    table_header = ParagraphStyle(
        "TableHeader",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        fontName="Helvetica-Bold",
        textColor=colors.white,
    )
    table_header_right = ParagraphStyle(
        "TableHeaderRight",
        parent=table_header,
        alignment=2,
    )

    elements = []

    # 1. Hospital Header
    elements.append(Paragraph(hospital.name.upper(), hosp_name_style))
    sub_parts = []
    if getattr(hospital, "tagline", ""):
        sub_parts.append(hospital.tagline)
    if getattr(hospital, "city", ""):
        sub_parts.append(hospital.city)
    if getattr(hospital, "phone_number", ""):
        sub_parts.append(f"Tel: {hospital.phone_number}")
    if sub_parts:
        elements.append(Paragraph(" • ".join(sub_parts), hosp_sub_style))

    elements.append(Spacer(1, 2 * mm))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#0f766e"), spaceAfter=6))

    # 2. Report Title & Period
    elements.append(Paragraph(f"{period_label.upper()} MIS REPORT", title_style))
    meta_text = f"<b>Reporting Window:</b> {start_date} to {end_date} &nbsp;|&nbsp; <b>Generated:</b> {timezone.localtime().strftime('%d %b %Y, %I:%M %p')}"
    elements.append(Paragraph(meta_text, hosp_sub_style))
    elements.append(Spacer(1, 4 * mm))

    # 3. High-Level Executive KPI Grid
    calls = summary.get("calls", {})
    funnel = summary.get("enquiry_funnel", {})
    no_show = summary.get("no_show", {})
    footfall = summary.get("footfall_by_source", {})
    total_enquiries = sum(funnel.values()) if funnel else 0
    total_completed = sum(footfall.values()) if footfall else 0

    kpi_data = [
        [
            Paragraph("<b>TELEPHONY PERFORMANCE</b>", table_header),
            Paragraph("<b>LEADS & CONVERSIONS</b>", table_header),
            Paragraph("<b>APPOINTMENT DISPOSITION</b>", table_header),
        ],
        [
            Paragraph(
                f"• Calls Received: <b>{calls.get('received', 0) or 0}</b><br/>"
                f"• Answered: <b>{calls.get('answered', 0) or 0}</b><br/>"
                f"• Missed / Lost: <b>{calls.get('missed', 0) or 0}</b><br/>"
                f"• Pending Callbacks: <b>{summary.get('pending_callbacks', 0)}</b>",
                table_cell,
            ),
            Paragraph(
                f"• Total Pipeline Leads: <b>{total_enquiries}</b><br/>"
                f"• Visited / Converted: <b>{funnel.get('visited', 0) or funnel.get('completed', 0) or 0}</b><br/>"
                f"• Scheduled Leads: <b>{funnel.get('scheduled', 0)}</b><br/>"
                f"• Lost Enquiries: <b>{funnel.get('lost', 0)}</b>",
                table_cell,
            ),
            Paragraph(
                f"• OPD Completed Visits: <b>{total_completed}</b><br/>"
                f"• No-Shows Logged: <b>{no_show.get('no_shows', 0)}</b><br/>"
                f"• Recall Tasks Resolved: <b>{no_show.get('recall_tasks_done', 0)}</b>",
                table_cell,
            ),
        ],
    ]
    kpi_table = Table(kpi_data, colWidths=[60 * mm, 61 * mm, 61 * mm])
    kpi_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
                ("TOPPADDING", (0, 0), (-1, 0), 4),
                ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd5e1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ("TOPPADDING", (0, 1), (-1, 1), 6),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
            ]
        )
    )
    elements.append(kpi_table)
    elements.append(Spacer(1, 5 * mm))

    # 4. Department & Doctor Volume Table
    elements.append(Paragraph("DEPARTMENT & DOCTOR CLINICAL FOOTFALL", section_style))
    doc_table_data = [
        [
            Paragraph("Doctor Name", table_header),
            Paragraph("Department", table_header),
            Paragraph("Booked", table_header_right),
            Paragraph("Completed", table_header_right),
            Paragraph("No-Show", table_header_right),
        ]
    ]

    if dept_doctor_rows:
        for r in dept_doctor_rows[:18]:  # Keep within printable bounds
            doc_table_data.append(
                [
                    Paragraph(r.get("doctor__name") or "—", table_cell),
                    Paragraph(r.get("doctor__department__name") or "General", table_cell),
                    Paragraph(str(r.get("booked", 0)), table_cell_right),
                    Paragraph(str(r.get("completed", 0)), table_cell_right),
                    Paragraph(str(r.get("no_show", 0)), table_cell_right),
                ]
            )
    else:
        doc_table_data.append([Paragraph("No appointments recorded for this period.", table_cell), Paragraph("", table_cell), Paragraph("", table_cell), Paragraph("", table_cell), Paragraph("", table_cell)])

    doc_table = Table(doc_table_data, colWidths=[52 * mm, 50 * mm, 26 * mm, 27 * mm, 27 * mm])
    doc_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
                ("TOPPADDING", (0, 0), (-1, 0), 4),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#f1f5f9")),
                ("TOPPADDING", (0, 1), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
            ]
        )
    )
    elements.append(doc_table)
    elements.append(Spacer(1, 5 * mm))

    # 5. Lead Source & Revenue Attribution Table
    elements.append(Paragraph("PATIENT ACQUISITION & ATTRIBUTED REVENUE BY SOURCE", section_style))
    rev_table_data = [
        [
            Paragraph("Acquisition Channel / Source", table_header),
            Paragraph("Enquiries Captured", table_header_right),
            Paragraph("Conversions", table_header_right),
            Paragraph("Billed Revenue", table_header_right),
        ]
    ]

    total_billed = Decimal("0")
    if revenue_rows:
        for r in revenue_rows:
            amt = Decimal(str(r.get("billed_amount", 0)))
            total_billed += amt
            rev_table_data.append(
                [
                    Paragraph(str(r.get("source", "Other")).replace("_", " ").title(), table_cell),
                    Paragraph(str(r.get("enquiry_count", 0)), table_cell_right),
                    Paragraph(str(r.get("conversion_count", 0)), table_cell_right),
                    Paragraph(_money(amt), table_cell_right),
                ]
            )
        # Total Row
        rev_table_data.append(
            [
                Paragraph("<b>TOTAL ATTRIBUTED</b>", table_cell_bold),
                Paragraph(f"<b>{sum(r.get('enquiry_count', 0) for r in revenue_rows)}</b>", table_cell_right),
                Paragraph(f"<b>{sum(r.get('conversion_count', 0) for r in revenue_rows)}</b>", table_cell_right),
                Paragraph(f"<b>{_money(total_billed)}</b>", table_cell_right),
            ]
        )
    else:
        rev_table_data.append([Paragraph("No source attribution data available.", table_cell), Paragraph("", table_cell), Paragraph("", table_cell), Paragraph("", table_cell)])

    rev_table = Table(rev_table_data, colWidths=[62 * mm, 38 * mm, 38 * mm, 44 * mm])
    rev_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
                ("TOPPADDING", (0, 0), (-1, 0), 4),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#f1f5f9")),
                ("TOPPADDING", (0, 1), (-1, -1), 3.5),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 3.5),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f8fafc")),
            ]
        )
    )
    elements.append(rev_table)

    # Footer note
    elements.append(Spacer(1, 6 * mm))
    elements.append(
        Paragraph(
            "<i>Note: Management Information System (MIS) reports are automatically aggregated from telephony logs, OPD scheduling, and patient billing entries. Confidential — For internal hospital administrative and management review only.</i>",
            hosp_sub_style,
        )
    )

    doc.build(elements)
    return buffer.getvalue()
