"""Payslip print (MedNet M2.22 HR: employee prefix on the salary slip) and the
salary-structure Excel download with gross amount and payable days."""
import calendar
import io
from datetime import date
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

LABELS = {
    "basic": "Basic", "hra": "HRA", "special_allowance": "Special allowance", "other_allowances": "Other allowances", "overtime": "Overtime",
    "pf": "Provident fund", "esi": "ESI", "professional_tax": "Professional tax", "tds": "Income tax (TDS)",
}


def employee_display_name(emp):
    """'Ms. Neha Bhosale' — the prefix printed on the slip."""
    user = emp.user
    name = (user.get_full_name() if user else "") or emp.designation
    return f"{emp.name_prefix} {name}".strip() if emp.name_prefix else name


def _money(v):
    return f"{Decimal(v or 0):,.2f}"


def _slip_story(slip, styles):
    emp, run = slip.employee, slip.run
    hospital = run.hospital
    head = ParagraphStyle("h", parent=styles["Title"], fontSize=14, spaceAfter=2)
    small = ParagraphStyle("s", parent=styles["Normal"], fontSize=8.5, textColor=colors.HexColor("#475569"))
    story = [Paragraph(hospital.name, head), Paragraph(f"Payslip for {run.month:%B %Y}", styles["Heading3"]), Spacer(1, 3 * mm)]
    info = [
        ["Employee", employee_display_name(emp), "Employee code", emp.employee_code],
        ["Designation", emp.designation, "Department", emp.department.name if emp.department_id else ""],
        ["Days in month", str(slip.days_in_month), "Paid days", f"{slip.paid_days}"],
        ["PAN", emp.pan_number or "—", "Bank a/c", ("XXXX" + emp.bank_account_number[-4:]) if emp.bank_account_number else "—"],
    ]
    t = Table(info, colWidths=[32 * mm, 58 * mm, 32 * mm, 48 * mm])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#64748b")),
                           ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#64748b")), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    story += [t, Spacer(1, 4 * mm)]
    earn = [(LABELS.get(k, k.replace("_", " ").capitalize()), v) for k, v in (slip.earnings or {}).items() if Decimal(v or 0)]
    ded = [(LABELS.get(k, k.replace("_", " ").capitalize()), v) for k, v in (slip.deductions or {}).items() if Decimal(v or 0)]
    rows = [["Earnings", "Amount (Rs.)", "Deductions", "Amount (Rs.)"]]
    for i in range(max(len(earn), len(ded), 1)):
        e = earn[i] if i < len(earn) else ("", "")
        d = ded[i] if i < len(ded) else ("", "")
        rows.append([e[0], _money(e[1]) if e[0] else "", d[0], _money(d[1]) if d[0] else ""])
    rows.append(["Gross earnings", _money(slip.gross), "Total deductions", _money(slip.total_deductions)])
    t = Table(rows, colWidths=[50 * mm, 35 * mm, 50 * mm, 35 * mm])
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b2340")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("ALIGN", (3, 0), (3, -1), "RIGHT"), ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.HexColor("#0b2340")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("GRID", (0, 0), (-1, -2), 0.25, colors.HexColor("#e2e8f0")),
    ]))
    story += [t, Spacer(1, 4 * mm), Paragraph(f"<b>Net pay: Rs. {_money(slip.net_pay)}</b>", styles["Heading2"])]
    if slip.employer_contributions:
        contrib = ", ".join(f"{LABELS.get(k, k)} Rs. {_money(v)}" for k, v in slip.employer_contributions.items() if Decimal(v or 0))
        if contrib:
            story.append(Paragraph(f"Employer contributions (not deducted): {contrib}", small))
    story += [Spacer(1, 6 * mm), Paragraph("This is a computer-generated payslip and does not need a signature.", small)]
    return story


def render_payslips(slips) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm, title="Payslips")
    styles = getSampleStyleSheet()
    story = []
    for n, slip in enumerate(slips):
        if n:
            story.append(PageBreak())
        story += _slip_story(slip, styles)
    if not story:
        story = [Paragraph("No payslips.", styles["Normal"])]
    doc.build(story)
    return buf.getvalue()


def salary_structure_rows(hospital_id, month: date):
    """One row per salary structure: monthly gross (fetched from the
    structure) and payable days for the month — from the month's payroll
    run if there is one, else days − absences − unpaid leave."""
    from .leave import unpaid_days
    from .models import Attendance
    from .models_hrm import Payslip, SalaryStructure

    month = month.replace(day=1)
    days = calendar.monthrange(month.year, month.month)[1]
    end = month.replace(day=days)
    slips = {p.employee_id: p for p in Payslip.objects.filter(run__hospital_id=hospital_id, run__month=month)}
    rows = [["Employee code", "Prefix", "Employee", "Designation", "Basic", "HRA", "Special allowance", "Other allowances", "Gross amount",
             "Days in month", "Payable days", "PF", "ESI", "Professional tax", "Monthly TDS"]]
    for st in SalaryStructure.objects.filter(hospital_id=hospital_id).select_related("employee__user").order_by("employee__employee_code"):
        emp = st.employee
        gross = st.basic + st.hra + st.special_allowance + st.other_allowances
        if emp.pk in slips:
            payable = slips[emp.pk].paid_days
        else:
            absent = Attendance.objects.filter(employee=emp, date__range=(month, end), status="absent").count()
            payable = max(Decimal(days - absent) - unpaid_days(emp, month, end), Decimal("0"))
        name = emp.user.get_full_name() if emp.user else ""
        rows.append([emp.employee_code, emp.name_prefix, name, emp.designation, float(st.basic), float(st.hra), float(st.special_allowance),
                     float(st.other_allowances), float(gross), days, float(payable), "Yes" if st.pf_applicable else "No",
                     "Yes" if st.esi_applicable else "No", float(st.professional_tax), float(st.monthly_tds)])
    return rows

