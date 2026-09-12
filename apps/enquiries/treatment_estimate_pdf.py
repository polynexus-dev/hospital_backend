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


def render_treatment_estimate_pdf(estimate) -> bytes:
    """Renders a patient-facing Treatment Cost Estimate / Financial Counseling quote sheet."""
    hospital = estimate.hospital
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
        "EstimateTitle",
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
        fontSize=9,
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
    contact_str = "  |  ".join(contact_parts) or "Multi-Speciality Tertiary Healthcare Center"
    story.append(Paragraph(contact_str, hosp_sub_style))
    story.append(Spacer(1, 3 * mm))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#0f766e"), spaceAfter=6))

    # 2. Estimate Title & Validity Banner
    valid_date_str = estimate.valid_until.strftime("%d %b %Y") if estimate.valid_until else "Valid for 30 Days"
    created_date_str = estimate.created_at.strftime("%d %b %Y") if estimate.created_at else timezone.now().strftime("%d %b %Y")
    estimate_no = f"EST-{estimate.pk:05d}"

    story.append(Paragraph("TREATMENT COST ESTIMATE & SURGICAL COUNSELING SHEET", title_style))
    story.append(Spacer(1, 1.5 * mm))

    # Reference Bar
    ref_table = Table(
        [
            [
                Paragraph(f"<b>Estimate Ref:</b> {estimate_no}", val_style),
                Paragraph(f"<b>Date:</b> {created_date_str}", val_style),
                Paragraph(f"<b>Validity:</b> {valid_date_str}", val_style),
                Paragraph(f"<b>Stage:</b> {estimate.get_stage_display()}", bold_val_style),
            ]
        ],
        colWidths=[45 * mm, 40 * mm, 45 * mm, 50 * mm],
    )
    ref_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("PADDING", (0, 0), (-1, -1), 4),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(ref_table)
    story.append(Spacer(1, 3 * mm))

    # 3. Patient & Clinical Details Grid
    patient_name = estimate.patient.full_name if estimate.patient else (estimate.enquiry.name if estimate.enquiry else "Prospective Patient")
    patient_uhid = getattr(estimate.patient, "uhid", "-") or "-"
    patient_mobile = getattr(estimate.patient, "mobile", "-") or (getattr(estimate.enquiry, "mobile", "-") if estimate.enquiry else "-")
    doctor_name = f"Dr. {estimate.doctor.user.get_full_name() or estimate.doctor.user.username}" if estimate.doctor and estimate.doctor.user else (estimate.doctor.name if hasattr(estimate.doctor, "name") else "Chief Surgeon")
    dept_name = estimate.department.name if estimate.department else "Surgical Specialties"

    patient_grid = [
        [
            Paragraph("Patient Name", label_style),
            Paragraph(patient_name, bold_val_style),
            Paragraph("Consulting Doctor", label_style),
            Paragraph(doctor_name, bold_val_style),
        ],
        [
            Paragraph("UHID / Ref ID", label_style),
            Paragraph(patient_uhid, val_style),
            Paragraph("Department", label_style),
            Paragraph(dept_name, val_style),
        ],
        [
            Paragraph("Contact Mobile", label_style),
            Paragraph(patient_mobile, val_style),
            Paragraph("Room Category", label_style),
            Paragraph(estimate.get_room_category_display(), bold_val_style),
        ],
        [
            Paragraph("Procedure Advised", label_style),
            Paragraph(f"<b>{estimate.procedure_name}</b>", bold_val_style),
            Paragraph("Expected Stay", label_style),
            Paragraph(f"{estimate.stay_days} Days", val_style),
        ],
    ]
    if estimate.diagnosis:
        patient_grid.append([
            Paragraph("Clinical Diagnosis", label_style),
            Paragraph(estimate.diagnosis, val_style),
            Paragraph("Payment Mode", label_style),
            Paragraph(f"{estimate.get_payment_mode_display()} ({estimate.tpa_name or 'Direct'})", val_style),
        ])

    p_table = Table(patient_grid, colWidths=[35 * mm, 55 * mm, 40 * mm, 50 * mm])
    p_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#f1f5f9")),
                ("PADDING", (0, 0), (-1, -1), 4),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f8fafc")),
                ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f8fafc")),
            ]
        )
    )
    story.append(p_table)
    story.append(Spacer(1, 4 * mm))

    # 4. Itemized Cost Breakdown Table
    story.append(Paragraph("ESTIMATED TREATMENT COST BREAKDOWN", section_style))

    cost_rows = [
        ["Sr.", "Cost Component / Service Description", "Amount (INR)"],
        ["1.", "Surgeon, Anesthetist & Surgical Team Professional Fees", _money(estimate.surgeon_fee)],
        ["2.", "Operation Theatre (OT), Recovery Room & Instrumentation Charges", _money(estimate.ot_charges)],
        ["3.", f"Room Rent & Inpatient Nursing Care ({estimate.get_room_category_display()} x {estimate.stay_days} days)", _money(estimate.room_charges)],
        ["4.", "Estimated Standard Medicines, IV Fluids, Disposables & Consumables", _money(estimate.medicines_estimate)],
        ["5.", "Implants, Prosthetics, Stents & Pre-op Diagnostic Investigations", _money(estimate.implants_investigations)],
        ["", "TOTAL ESTIMATED TREATMENT PACKAGE", _money(estimate.total_estimate)],
    ]

    c_table = Table(cost_rows, colWidths=[12 * mm, 128 * mm, 40 * mm])
    c_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 8.5),
                ("ALIGN", (2, 0), (2, -1), "RIGHT"),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("FONTSIZE", (0, 1), (-1, -2), 8),
                ("PADDING", (0, 0), (-1, -1), 4.5),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("INNERGRID", (0, 0), (-1, -2), 0.5, colors.HexColor("#e2e8f0")),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f0fdfa")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, -1), (-1, -1), 9.5),
                ("TEXTCOLOR", (0, -1), (-1, -1), colors.HexColor("#0f766e")),
                ("LINEABOVE", (0, -1), (-1, -1), 1.5, colors.HexColor("#0f766e")),
            ]
        )
    )
    story.append(c_table)
    story.append(Spacer(1, 3.5 * mm))

    # 5. Insurance & Pre-Authorization Status (if cashless/insurance)
    if estimate.payment_mode in [estimate.PaymentMode.INSURANCE_CASHLESS, estimate.PaymentMode.GOVT_SCHEME, estimate.PaymentMode.CORPORATE]:
        preauth_status_color = "#0284c7" if estimate.insurance_preauth_status == estimate.PreAuthStatus.APPROVED else "#d97706"
        preauth_box = [
            [
                Paragraph("<b>Insurance / TPA Desk Status</b>", label_style),
                Paragraph(f"TPA / Payer: <b>{estimate.tpa_name or 'Private Insurance'}</b>", val_style),
                Paragraph(f"Pre-Auth Status: <b>{estimate.get_insurance_preauth_status_display()}</b>", bold_val_style),
                Paragraph(f"Approved Amount: <b>{_money(estimate.approved_preauth_amount or 0)}</b>" if estimate.approved_preauth_amount else "Awaiting Final Sanction", val_style),
            ]
        ]
        preauth_table = Table(preauth_box, colWidths=[40 * mm, 45 * mm, 50 * mm, 45 * mm])
        preauth_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f0f9ff")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#7dd3fc")),
                    ("PADDING", (0, 0), (-1, -1), 4),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        story.append(preauth_table)
        story.append(Spacer(1, 3 * mm))

    # 6. Terms & Medical Disclaimer
    story.append(Paragraph("TERMS & FINANCIAL COUNSELING GUIDELINES", section_style))
    terms_text = (
        "1. <b>Estimate Validity:</b> This proforma estimate is valid for 30 days from the date of issue.<br/>"
        "2. <b>Scope of Package:</b> Covers standard surgeon fees, OT charges, specified room category stay, and routine medications for the duration stated above.<br/>"
        "3. <b>Exclusions & Extra Charges:</b> Stays beyond the estimated days, post-operative ICU transfer, unexpected emergency interventions, high-end specialized implants, non-medical comfort consumables, or treatment of co-morbidities (e.g. uncontrolled diabetes, cardiac monitoring) will be billed at actuals.<br/>"
        "4. <b>Cashless Insurance:</b> Cashless facility is subject to approval from the TPA / Insurer. Co-payments, non-payable items, and deductions must be settled by the patient upon admission/discharge.<br/>"
        "5. <b>Admission Deposit:</b> An initial admission deposit of 50% of the estimated package is required at the time of admission for self-paying patients."
    )
    story.append(Paragraph(terms_text, disclaimer_style))
    story.append(Spacer(1, 8 * mm))

    # 7. Signature Blocks
    sig_data = [
        [
            Paragraph("____________________________________<br/><b>Patient / Relative Signature</b><br/>I have understood the financial estimate and terms.", disclaimer_style),
            Paragraph("____________________________________<br/><b>Medical Financial Counselor</b><br/>Lifecare Admission & Counseling Desk", disclaimer_style),
            Paragraph("____________________________________<br/><b>Authorized Hospital Signatory</b><br/>Billing & TPA Department", disclaimer_style),
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
