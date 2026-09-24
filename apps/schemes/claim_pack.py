"""Claim pack PDF — everything the claims desk uploads to the scheme portal, on one sheet."""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _table(rows, widths=None):
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c8d0da")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def render_claim_pack(case):
    from apps.billing.models import Bill

    styles = getSampleStyleSheet()
    scheme, ben, patient, adm = case.scheme, case.beneficiary, case.beneficiary.patient, case.admission
    story = [
        Paragraph(f"{scheme.name} — claim pack", styles["Title"]),
        Paragraph(f"{case.hospital.name} · empanelment {scheme.empanelment_number or '—'}", styles["Normal"]),
        Spacer(1, 4 * mm),
        _table([
            ["Beneficiary", f"{patient.full_name} ({patient.uhid})", "Beneficiary ID", ben.beneficiary_id],
            ["Family ID", ben.family_id or "—", "Relation", ben.relation or "—"],
            ["Admitted", adm.admitted_at.strftime("%d %b %Y %H:%M") if adm else "—", "Discharged", adm.discharged_at.strftime("%d %b %Y %H:%M") if adm and adm.discharged_at else "—"],
            ["Diagnosis", case.diagnosis or (getattr(getattr(adm, "discharge_summary", None), "final_diagnosis", "") if adm else "") or "—", "Status", case.get_status_display()],
            ["Pre-auth no.", case.preauth_number or "—", "Pre-auth approved", f"₹{case.preauth_amount_approved:,.2f}"],
            ["Claim no.", case.claim_number or "—", "Claim due by", str(case.claim_due_by or "—")],
        ], widths=[30 * mm, 60 * mm, 32 * mm, 58 * mm]),
        Spacer(1, 5 * mm),
    ]
    packages = [[cp.package.code, cp.package.name, str(cp.quantity), f"₹{cp.rate:,.2f}", f"₹{cp.rate * cp.quantity:,.2f}"] for cp in case.case_packages.select_related("package")]
    if packages:
        story += [Paragraph("Packages", styles["Heading3"]), _table([["Code", "Package", "Qty", "Rate", "Amount"], *packages], [25 * mm, 90 * mm, 12 * mm, 26 * mm, 27 * mm]), Spacer(1, 5 * mm)]
    bill = Bill.objects.filter(admission=adm, is_interim=False).exclude(status="cancelled").order_by("created_at").first() if adm else None
    if bill:
        items = [[i.description[:70], str(i.quantity), f"₹{i.unit_price:,.2f}", f"₹{i.total_price:,.2f}"] for i in bill.items.all()]
        story += [
            Paragraph(f"Bill {bill.bill_number}", styles["Heading3"]),
            _table([["Item", "Qty", "Rate", "Amount"], *items, ["Net", "", "", f"₹{bill.net_amount:,.2f}"]], [110 * mm, 12 * mm, 29 * mm, 29 * mm]),
            Spacer(1, 5 * mm),
        ]
    story += [
        Paragraph(f"Claim amount: ₹{case.claim_amount:,.2f}", styles["Heading3"]),
        Paragraph("Documents", styles["Heading3"]),
        _table([["Document", "Attached"], *[[d, "✓" if case.documents.get(d) else "missing"] for d in scheme.document_checklist]], [140 * mm, 40 * mm]),
    ]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm).build(story)
    return buf.getvalue()
