from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet


def _format_patient_age_gender(patient) -> str:
    parts = []
    if getattr(patient, "gender", None):
        parts.append(patient.get_gender_display() if hasattr(patient, "get_gender_display") else str(patient.gender).capitalize())
    if getattr(patient, "date_of_birth", None):
        from datetime import date
        today = date.today()
        dob = patient.date_of_birth
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        parts.append(f"{age} Yrs")
    return " / ".join(parts) if parts else "N/A"


def render_prescription_pdf(prescription) -> bytes:
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
    hospital = prescription.hospital
    patient = prescription.patient
    doctor = prescription.doctor

    hosp_name_style = ParagraphStyle(
        "HospName",
        parent=styles["Heading1"],
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#0f766e"), # Deep Teal
        alignment=1, # Centered
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
        "RxTitle",
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
        textColor=colors.HexColor("#0f766e"),
        spaceBefore=6,
        spaceAfter=3,
    )
    cell_bold = ParagraphStyle("CellBold", parent=styles["Normal"], fontSize=9, leading=12, fontName="Helvetica-Bold")
    cell_norm = ParagraphStyle("CellNorm", parent=styles["Normal"], fontSize=9, leading=12)
    rx_symbol_style = ParagraphStyle(
        "RxSymbol",
        parent=styles["Heading1"],
        fontSize=24,
        leading=26,
        textColor=colors.HexColor("#0f766e"),
        fontName="Helvetica-Bold",
    )

    elements = []

    # 1. Hospital Header
    hosp_name = getattr(hospital, "name", "Hospital Management System")
    hosp_location = f"{getattr(hospital, 'address', '')} {getattr(hospital, 'city', '')} {getattr(hospital, 'state', '')}".strip()
    elements.append(Paragraph(hosp_name.upper(), hosp_name_style))
    if hosp_location:
        elements.append(Paragraph(hosp_location, hosp_sub_style))
    elements.append(Spacer(1, 3 * mm))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#0f766e"), spaceBefore=2, spaceAfter=6))

    # 2. Document Title
    elements.append(Paragraph("OUTPATIENT ELECTRONIC PRESCRIPTION (e-Rx)", title_style))
    elements.append(Spacer(1, 3 * mm))

    # 3. Patient & Doctor Information Box
    dr_name = f"Dr. {doctor.first_name} {doctor.last_name}".strip() if doctor else "Attending Medical Officer"
    pt_name = getattr(patient, "full_name", f"{getattr(patient, 'first_name', '')} {getattr(patient, 'last_name', '')}".strip())
    uhid = getattr(patient, "uhid", "N/A")
    age_gender = _format_patient_age_gender(patient)
    rx_date = prescription.created_at.strftime("%d-%b-%Y %I:%M %p") if prescription.created_at else "N/A"

    info_data = [
        [
            Paragraph("<b>Patient Name:</b>", cell_norm),
            Paragraph(pt_name, cell_bold),
            Paragraph("<b>Prescription Date:</b>", cell_norm),
            Paragraph(rx_date, cell_norm),
        ],
        [
            Paragraph("<b>UHID / Reg No:</b>", cell_norm),
            Paragraph(f"<font color='#0f766e'><b>{uhid}</b></font>", cell_bold),
            Paragraph("<b>Consulting Doctor:</b>", cell_norm),
            Paragraph(dr_name, cell_bold),
        ],
        [
            Paragraph("<b>Age / Gender:</b>", cell_norm),
            Paragraph(age_gender, cell_norm),
            Paragraph("<b>Department:</b>", cell_norm),
            Paragraph("General OPD / Consultation", cell_norm),
        ],
    ]

    info_table = Table(info_data, colWidths=[32 * mm, 55 * mm, 38 * mm, 49 * mm])
    info_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#cbd5e1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 4 * mm))

    # 4. Clinical Evaluation (Diagnosis & Symptoms)
    diag_text = getattr(prescription, "diagnosis", "") or "Under clinical evaluation"
    symp_text = getattr(prescription, "symptoms", "") or "None recorded"

    diag_data = [
        [Paragraph("<b>Clinical Diagnosis:</b>", cell_bold), Paragraph(diag_text, cell_norm)],
        [Paragraph("<b>Presenting Complaints / Symptoms:</b>", cell_bold), Paragraph(symp_text, cell_norm)],
    ]
    diag_table = Table(diag_data, colWidths=[55 * mm, 119 * mm])
    diag_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(diag_table)
    elements.append(Spacer(1, 4 * mm))

    # 5. Medications Section
    elements.append(Paragraph("Rx — Prescribed Medications", section_head_style))

    raw_meds = getattr(prescription, "medications", [])
    if isinstance(raw_meds, list) and len(raw_meds) > 0:
        med_rows = [
            [
                Paragraph("<b>#</b>", cell_bold),
                Paragraph("<b>Medicine Name & Strength</b>", cell_bold),
                Paragraph("<b>Dosage / Frequency</b>", cell_bold),
                Paragraph("<b>Duration</b>", cell_bold),
                Paragraph("<b>Instructions</b>", cell_bold),
            ]
        ]
        for idx, item in enumerate(raw_meds, start=1):
            if isinstance(item, dict):
                m_name = item.get("name") or item.get("medicine") or "Medicine"
                m_dose = item.get("dosage") or item.get("strength") or "-"
                m_freq = item.get("frequency") or item.get("timing") or "Once daily"
                m_dur = item.get("duration") or "-"
                m_inst = item.get("instructions") or item.get("notes") or "After meals"
                med_rows.append([
                    Paragraph(str(idx), cell_norm),
                    Paragraph(f"<b>{m_name}</b> {m_dose}", cell_norm),
                    Paragraph(m_freq, cell_norm),
                    Paragraph(m_dur, cell_norm),
                    Paragraph(m_inst, cell_norm),
                ])
            else:
                med_rows.append([
                    Paragraph(str(idx), cell_norm),
                    Paragraph(str(item), cell_norm),
                    Paragraph("-", cell_norm),
                    Paragraph("-", cell_norm),
                    Paragraph("As directed", cell_norm),
                ])

        med_table = Table(med_rows, colWidths=[10 * mm, 64 * mm, 38 * mm, 26 * mm, 36 * mm])
        med_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#cbd5e1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(med_table)
    else:
        elements.append(Paragraph("<i>No prescription medications prescribed in this encounter.</i>", cell_norm))

    elements.append(Spacer(1, 4 * mm))

    # 6. Diagnostic Tests & Investigations (if any)
    raw_labs = getattr(prescription, "lab_orders", [])
    if isinstance(raw_labs, list) and len(raw_labs) > 0:
        elements.append(Paragraph("Recommended Laboratory & Diagnostic Investigations", section_head_style))
        lab_items = []
        for lab in raw_labs:
            if isinstance(lab, dict):
                t_name = lab.get("test_name") or lab.get("name") or str(lab)
                t_notes = lab.get("notes", "")
                lab_items.append(f"• <b>{t_name}</b>" + (f" ({t_notes})" if t_notes else ""))
            else:
                lab_items.append(f"• <b>{lab}</b>")
        elements.append(Paragraph("<br/>".join(lab_items), cell_norm))
        elements.append(Spacer(1, 3 * mm))

    # 7. Additional Doctor Advice / Notes
    notes = getattr(prescription, "notes", "")
    if notes:
        elements.append(Paragraph("Doctor's Advice & Precautions", section_head_style))
        elements.append(Paragraph(notes, cell_norm))
        elements.append(Spacer(1, 3 * mm))

    # 8. Signature Block & Disclaimer
    elements.append(Spacer(1, 10 * mm))
    sig_data = [
        [
            Paragraph("<font color='#64748b' size=7>This is a computer generated electronic prescription (e-Rx).<br/>Valid under Indian Telemedicine & Digital Health Guidelines.</font>", cell_norm),
            Paragraph(f"<b>{dr_name}</b><br/><font color='#64748b'>Authorized Medical Practitioner<br/>Registration Verified</font>", ParagraphStyle("DrSig", parent=cell_norm, alignment=2)),
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
