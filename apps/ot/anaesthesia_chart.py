"""Printable anaesthesia chart (A4): case details, a vitals graph over the
case with induction / incision / end / reversal marked, the vitals, drug
and fluid tables, recovery details and the anaesthetist's signature block.

Vitals rows are free JSON ({time, hr, bp, spo2, etco2, ...}); `time` may be
"HH:MM" or an ISO date-time, and `bp` "120/80" (or sbp / dbp keys). Rows
whose time can't be read still print in the table, just not on the graph.
"""
import io
import re
from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from reportlab.graphics.shapes import Drawing, Line, PolyLine, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.clinical.sign_block import signature_block

INK = colors.HexColor("#1f2937")
MUTED = colors.HexColor("#6b7280")
GRID = colors.HexColor("#e5e7eb")
SERIES = {  # key: (label, colour)
    "sbp": ("Systolic BP", colors.HexColor("#b91c1c")),
    "dbp": ("Diastolic BP", colors.HexColor("#ef4444")),
    "hr": ("Heart rate", colors.HexColor("#1d4ed8")),
    "spo2": ("SpO2 %", colors.HexColor("#047857")),
    "etco2": ("EtCO2", colors.HexColor("#7c3aed")),
}
VITALS_PER_ROW = 12
TABLE_STYLE = TableStyle([
    ("FONTSIZE", (0, 0), (-1, -1), 8),
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
])


def _num(v):
    if v is None or v == "":
        return None
    m = re.search(r"-?\d+(\.\d+)?", str(v))
    return float(m.group()) if m else None


def _bp(row):
    if row.get("sbp") not in (None, "") or row.get("dbp") not in (None, ""):
        return _num(row.get("sbp")), _num(row.get("dbp"))
    m = re.match(r"\s*(\d+)\s*/\s*(\d+)", str(row.get("bp") or ""))
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


def _local(dt):
    return timezone.localtime(dt) if timezone.is_aware(dt) else dt


def _minutes(value, base):
    """Minutes since midnight of `base` (a date), or None."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = _local(value)
    else:
        s = str(value).strip()
        m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", s)
        if m:
            return int(m.group(1)) * 60 + int(m.group(2))
        dt = parse_datetime(s)
        if dt is None:
            return None
        dt = _local(dt)
    return (dt - datetime.combine(base, datetime.min.time(), tzinfo=dt.tzinfo)).total_seconds() / 60


def _clock(minutes):
    minutes = int(round(minutes)) % 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _fmt_dt(dt):
    return _local(dt).strftime("%d %b %Y %H:%M") if dt else "—"


def _fmt_time(dt):
    return _local(dt).strftime("%H:%M") if dt else "—"


def vitals_points(vitals, base):
    """[(minute, {series: value})] in time order; readings past midnight
    that are given as "HH:MM" roll on to the next day."""
    points, first = [], None
    for row in vitals or []:
        if not isinstance(row, dict):
            continue
        t = _minutes(row.get("time"), base)
        if t is None:
            continue
        if first is None:
            first = t
        elif t < first - 720:
            t += 1440
        sbp, dbp = _bp(row)
        vals = {"sbp": sbp, "dbp": dbp, "hr": _num(row.get("hr")), "spo2": _num(row.get("spo2")), "etco2": _num(row.get("etco2"))}
        points.append((t, {k: v for k, v in vals.items() if v is not None}))
    return sorted(points, key=lambda p: p[0])


def vitals_graph(points, events, width=180 * mm, height=72 * mm):
    """A Drawing, or None with fewer than two timed readings."""
    if len(points) < 2:
        return None
    left, right, bottom, top = 12 * mm, 4 * mm, 10 * mm, 8 * mm
    pw, ph = width - left - right, height - bottom - top
    t0, t1 = points[0][0], points[-1][0]
    for _label, t in events:
        t0, t1 = min(t0, t), max(t1, t)
    span = max(t1 - t0, 10)
    vmax = max([v for _t, vals in points for v in vals.values()] + [200])
    ymax = int((vmax + 19) // 20 * 20)

    def x(t):
        return left + (t - t0) / span * pw

    def y(v):
        return bottom + v / ymax * ph

    d = Drawing(width, height)
    d.add(Rect(left, bottom, pw, ph, strokeColor=GRID, fillColor=None, strokeWidth=0.5))
    for v in range(0, ymax + 1, 20):
        d.add(Line(left, y(v), left + pw, y(v), strokeColor=GRID, strokeWidth=0.3))
        d.add(String(left - 2, y(v) - 2.5, str(v), fontName="Helvetica", fontSize=6.5, fillColor=MUTED, textAnchor="end"))
    step = next(s for s in (5, 10, 15, 30, 60, 120, 240) if span / s <= 12)
    tick = (int(t0) // step + (1 if t0 % step else 0)) * step
    while tick <= t1:
        d.add(Line(x(tick), bottom, x(tick), bottom + ph, strokeColor=GRID, strokeWidth=0.3))
        d.add(String(x(tick), bottom - 8, _clock(tick), fontName="Helvetica", fontSize=6.5, fillColor=MUTED, textAnchor="middle"))
        tick += step
    for label, t in events:
        d.add(Line(x(t), bottom, x(t), bottom + ph, strokeColor=INK, strokeWidth=0.6, strokeDashArray=[2, 2]))
        d.add(String(x(t), bottom + ph + 1.5, label, fontName="Helvetica", fontSize=6.5, fillColor=INK, textAnchor="middle"))
    for key, (_name, colour) in SERIES.items():
        pts = [(x(t), y(vals[key])) for t, vals in points if key in vals]
        if len(pts) >= 2:
            d.add(PolyLine([c for p in pts for c in p], strokeColor=colour, strokeWidth=1))
        for px, py in pts:
            if key == "sbp":  # the chart convention: systolic v, diastolic ^
                d.add(PolyLine([px - 2, py + 3, px, py, px + 2, py + 3], strokeColor=colour, strokeWidth=1))
            elif key == "dbp":
                d.add(PolyLine([px - 2, py - 3, px, py, px + 2, py - 3], strokeColor=colour, strokeWidth=1))
            elif key == "hr":
                d.add(Rect(px - 1.3, py - 1.3, 2.6, 2.6, fillColor=colour, strokeColor=colour))
            else:
                d.add(Polygon([px, py + 1.8, px + 1.8, py, px, py - 1.8, px - 1.8, py], fillColor=colour, strokeColor=colour))
    lx = left
    for key, (name, colour) in SERIES.items():
        if any(key in vals for _t, vals in points):
            d.add(Line(lx, height - 3, lx + 6, height - 3, strokeColor=colour, strokeWidth=1.5))
            d.add(String(lx + 8, height - 5, name, fontName="Helvetica", fontSize=6.5, fillColor=INK))
            lx += 8 + len(name) * 3.4 + 8
    return d


def _base_date(schedule, record):
    for dt in (record.induction_at, schedule.actual_start, schedule.wheeled_in_at, schedule.scheduled_start):
        if dt:
            return _local(dt).date()
    return timezone.localdate()


def render_anaesthesia_chart(record) -> bytes:
    schedule = record.ot_schedule
    req = schedule.surgery_request
    patient = req.patient
    hospital = record.hospital
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, leading=10)
    h3 = ParagraphStyle("h3", parent=styles["Heading3"], fontSize=10, spaceBefore=6, spaceAfter=3)
    base = _base_date(schedule, record)

    age = ""
    if patient.date_of_birth:
        dob = patient.date_of_birth
        today = timezone.localdate()
        age = f"{today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))} y"
    sex = patient.get_gender_display() if patient.gender else ""
    anaes = record.anaesthetist
    anaes_name = (f"Dr. {anaes.doctor_profile.name}" if getattr(anaes, "doctor_profile", None) else anaes.get_full_name() or anaes.email) if anaes else "—"

    story = [
        Paragraph(f"<b>{hospital.name}</b>", ParagraphStyle("hosp", parent=styles["Title"], fontSize=15, leading=18, spaceAfter=0)),
        Paragraph(hospital.address.replace("\n", ", ") if getattr(hospital, "address", "") else "", ParagraphStyle("addr", parent=small, alignment=1, textColor=MUTED)),
        Paragraph("Anaesthesia chart", ParagraphStyle("title", parent=styles["Heading2"], alignment=1, spaceBefore=4)),
    ]
    info = [
        ["Patient", f"{patient.full_name} ({patient.uhid or '—'})", "Age / sex", " / ".join(x for x in (age, sex) if x) or "—"],
        ["Procedure", Paragraph(req.proposed_procedure, small), "Encounter", dict(req.Encounter.choices)[req.encounter]],
        ["Surgeon", f"Dr. {schedule.surgeon.name}", "Anaesthetist", anaes_name],
        ["OT room", schedule.operation_theatre_room, "Date", base.strftime("%d %b %Y")],
        ["Anaesthesia", record.anaesthesia_type, "ASA grade", record.asa_grade or "—"],
        ["Airway", record.airway_device or "—", "Priority", req.priority.title()],
        ["Wheeled in", _fmt_time(schedule.wheeled_in_at), "Induction", _fmt_time(record.induction_at)],
        ["Incision", _fmt_time(schedule.actual_start), "Surgery end", _fmt_time(schedule.actual_end)],
        ["Reversal", _fmt_time(record.reversal_at), "Wheeled out", _fmt_time(schedule.wheeled_out_at)],
    ]
    t = Table(info, colWidths=[25 * mm, 65 * mm, 25 * mm, 65 * mm])
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8), ("TEXTCOLOR", (0, 0), (0, -1), MUTED), ("TEXTCOLOR", (2, 0), (2, -1), MUTED),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, GRID),
    ]))
    story += [t]

    points = vitals_points(record.vitals, base)
    events = [(label, m) for label, m in (
        ("Induction", _minutes(record.induction_at, base)), ("Incision", _minutes(schedule.actual_start, base)),
        ("End", _minutes(schedule.actual_end, base)), ("Reversal", _minutes(record.reversal_at, base)),
    ) if m is not None]
    if points:
        lo, hi = points[0][0] - 60, points[-1][0] + 60
        events = [(label, m) for label, m in events if lo <= m <= hi]
    graph = vitals_graph(points, events)
    story.append(Paragraph("Intra-operative vitals", h3))
    if graph is not None:
        story += [graph, Spacer(1, 2 * mm)]
    elif record.vitals:
        story.append(Paragraph("Fewer than two timed readings — no graph.", small))

    vitals = [r for r in (record.vitals or []) if isinstance(r, dict)]
    if vitals:
        # As on a paper chart: one column per reading, a row per parameter,
        # in blocks of VITALS_PER_ROW readings.
        extra = sorted({k for r in vitals for k in r} - {"time", "hr", "bp", "sbp", "dbp", "spo2", "etco2"})
        params = [("HR", "hr"), ("BP", "bp"), ("SpO2", "spo2"), ("EtCO2", "etco2")] + [(k.replace("_", " ").title(), k) for k in extra]
        timed = sorted(vitals, key=lambda r: _minutes(r.get("time"), base) if _minutes(r.get("time"), base) is not None else 1e9)

        def cell(r, key):
            if key == "bp":
                sbp, dbp = _bp(r)
                return str(r.get("bp") or (f"{sbp:g}/{dbp:g}" if sbp is not None and dbp is not None else ""))
            return str(r.get(key, ""))

        for i in range(0, len(timed), VITALS_PER_ROW):
            chunk = timed[i:i + VITALS_PER_ROW]
            times = [_clock(m) if (m := _minutes(r.get("time"), base)) is not None else str(r.get("time") or "") for r in chunk]
            data = [["Time"] + times] + [[label] + [cell(r, key) for r in chunk] for label, key in params]
            vt = Table(data, colWidths=[16 * mm] + [13.6 * mm] * len(chunk), hAlign="LEFT")
            vt.setStyle(TABLE_STYLE)
            vt.setStyle(TableStyle([("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"), ("ALIGN", (1, 0), (-1, -1), "CENTER")]))
            story += [vt, Spacer(1, 2 * mm)]
    else:
        story.append(Paragraph("No vitals recorded.", small))

    drugs = [r for r in (record.drugs or []) if isinstance(r, dict)]
    story.append(Paragraph("Drugs given", h3))
    if drugs:
        dt = Table([["Time", "Drug", "Dose", "Route"]] + [
            [(_clock(m) if (m := _minutes(r.get("time"), base)) is not None else str(r.get("time") or "")), Paragraph(str(r.get("drug", "")), small), str(r.get("dose", "")), str(r.get("route", ""))]
            for r in drugs], colWidths=[18 * mm, 92 * mm, 40 * mm, 30 * mm], repeatRows=1, hAlign="LEFT")
        dt.setStyle(TABLE_STYLE)
        story.append(dt)
    else:
        story.append(Paragraph("None recorded.", small))

    fluids = [r for r in (record.fluids or []) if isinstance(r, dict)]
    fluids_heading = Paragraph("Fluids & output", h3)
    io_rows = [[f.get("fluid", ""), f"{f.get('volume_ml', '')} mL"] for f in fluids]
    total_in = sum((_num(f.get("volume_ml")) or 0) for f in fluids)
    if fluids:
        io_rows.append(["Total in", f"{total_in:g} mL"])
    io_rows += [["Estimated blood loss", f"{record.estimated_blood_loss_ml} mL" if record.estimated_blood_loss_ml is not None else "—"],
                ["Urine output", f"{record.urine_output_ml} mL" if record.urine_output_ml is not None else "—"]]
    ft = Table([["Fluid / output", "Volume"]] + io_rows, colWidths=[110 * mm, 40 * mm], hAlign="LEFT")
    ft.setStyle(TABLE_STYLE)
    if fluids:
        ft.setStyle(TableStyle([("FONTNAME", (0, len(fluids) + 1), (-1, len(fluids) + 1), "Helvetica-Bold")]))
    story.append(KeepTogether([fluids_heading, ft]))

    story.append(Paragraph("Recovery", h3))
    rec = Table([
        ["Complications", Paragraph(record.complications or "None", small)],
        ["Aldrete score", str(record.recovery_aldrete_score) if record.recovery_aldrete_score is not None else "—"],
        ["Shifted to", record.shifted_to or "—"],
        ["Notes", Paragraph((record.intra_op_notes or "—").replace("\n", "<br/>"), small)],
    ], colWidths=[30 * mm, 150 * mm], hAlign="LEFT")
    rec.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8), ("TEXTCOLOR", (0, 0), (0, -1), MUTED), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(rec)

    status = (f"Finalised {_fmt_dt(record.finalized_at)}" if record.finalized_at else "DRAFT — not finalised")
    sign = signature_block(anaes, name=anaes_name, subtitle="Anaesthetist") if anaes else None
    tail = [Spacer(1, 6 * mm)]
    if sign is not None:
        tail.append(sign)
    tail.append(Paragraph(f"<i>{status}. Printed {_fmt_dt(timezone.now())}.</i>", ParagraphStyle("foot", parent=small, textColor=MUTED, spaceBefore=4)))
    story.append(KeepTogether(tail))

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
                      title=f"Anaesthesia chart – {patient.full_name}").build(story)
    return buf.getvalue()
