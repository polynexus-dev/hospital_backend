"""One report for several of a patient's verified orders (MedNet M2.22 lab
"auto report merge while preparing dispatch"): results grouped by lab
department, each department one table with a common header (Investigation,
Result, Unit, Reference range, Method), so a patient gets one document
rather than a PDF per order."""
import io

from django.utils import timezone

from .models import LabTest, LabSettings

DEPARTMENT_LABEL = dict(LabTest.Department.choices)


def render_patient_lab_report(orders):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    orders = sorted(orders, key=lambda o: o.ordered_at)
    first = orders[0]
    p, hospital = first.patient, first.hospital
    styles = getSampleStyleSheet()
    lab_settings = LabSettings.for_hospital(first.hospital_id)
    results = []
    for o in orders:
        results += list(o.results.select_related("lab_test", "finalized_by", "lab_test__report_template").order_by("lab_test__name"))
    nabl = lab_settings.nabl_active and any(r.lab_test.is_nabl_accredited for r in results)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=f"Lab report {p.uhid}")
    story = []
    if nabl and lab_settings.nabl_logo:
        try:
            story.append(Image(lab_settings.nabl_logo.path, width=40, height=40, hAlign="RIGHT"))
        except Exception:  # noqa: BLE001 - a missing logo file must not stop the report
            pass
    numbers = ", ".join(o.order_number for o in orders)
    story += [
        Paragraph(f"<b>{hospital.name}</b> — Laboratory Report", styles["Title"]),
        Paragraph(f"Patient: <b>{p.full_name}</b> &nbsp; UHID: {p.uhid} &nbsp; Orders: {numbers}", styles["Normal"]),
        Paragraph(f"Collected {timezone.localtime(first.ordered_at):%d %b %Y %H:%M} – {timezone.localtime(orders[-1].ordered_at):%d %b %Y %H:%M}", styles["Normal"]),
    ]
    if nabl:
        story.append(Paragraph(f"NABL accredited laboratory &middot; Certificate No. {lab_settings.nabl_certificate_number}", styles["Normal"]))
    story.append(Spacer(1, 8))
    by_dept = {}
    for r in results:
        by_dept.setdefault(r.lab_test.department or "other", []).append(r)
    all_signed = all(r.finalized_at for r in results) if results else False
    for dept in sorted(by_dept, key=lambda d: list(DEPARTMENT_LABEL).index(d) if d in DEPARTMENT_LABEL else 99):
        rows = [["Investigation", "Result", "Unit", "Reference range", "Method"]]  # the department's common header
        flagged = []
        for i, r in enumerate(by_dept[dept], start=1):
            name = r.lab_test.name + (" *" if nabl and r.lab_test.is_nabl_accredited else "")
            value = r.value + (" (amended)" if r.is_amended else "") + (f" {r.get_flag_display()}" if r.flag != "normal" else "")
            rows.append([name, value, r.unit or r.lab_test.unit, r.reference_range or r.lab_test.reference_range, r.lab_test.method or ""])
            if r.flag != "normal":
                flagged.append(i)
        t = Table(rows, colWidths=[160, 90, 55, 120, 85], repeatRows=1)
        style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1d4ed8")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                 ("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 9)]
        for i in flagged:
            style += [("TEXTCOLOR", (1, i), (1, i), colors.red), ("FONTNAME", (1, i), (1, i), "Helvetica-Bold")]
        t.setStyle(TableStyle(style))
        story += [KeepTogether([Paragraph(f"<b>{DEPARTMENT_LABEL.get(dept, dept).upper()}</b>", styles["Heading3"]), t]), Spacer(1, 8)]
    if nabl:
        story.append(Paragraph(f"* Within the scope of NABL accreditation (Certificate No. {lab_settings.nabl_certificate_number}). "
                               "Tests without * are outside the scope of accreditation.", styles["Italic"]))
    for o in orders:
        for a in o.addenda.all():
            story += [Spacer(1, 6), Paragraph(f"<b>Addendum to {o.order_number} ({timezone.localtime(a.created_at):%d %b %Y %H:%M}):</b> {a.text}", styles["Normal"])]
    story += [Spacer(1, 18), Paragraph("Electronically verified report." if all_signed else "<b>PROVISIONAL — not yet verified by pathologist.</b>", styles["Normal"])]
    signers = [r.finalized_by for r in results if r.finalized_by_id]
    if all_signed and signers:
        from apps.clinical.sign_block import signature_block

        designation = next((r.lab_test.report_template.signatory_designation for r in results if r.lab_test.report_template_id), "Consultant Pathologist")
        block = signature_block(signers[-1], subtitle=designation)
        if block is not None:
            story += [Spacer(1, 8), block]
    doc.build(story)
    return buf.getvalue()
