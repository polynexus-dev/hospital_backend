"""The signature block printed on clinical documents: the signer's
signature image and stamp/seal side by side at the sizes they chose
(User.signature_width_mm / stamp_width_mm), then name, designation and
council registration number."""
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, Table, TableStyle


def _image(field, width_mm):
    if not field:
        return None
    try:
        from reportlab.lib.utils import ImageReader

        path = field.path
        iw, ih = ImageReader(path).getSize()
        w = max(min(int(width_mm or 30), 80), 10) * mm
        return Image(path, width=w, height=w * ih / iw)
    except Exception:  # noqa: BLE001 - a missing / unreadable image must never stop the document
        return None


def signature_block(user, *, name="", subtitle="", align="RIGHT", font_size=8):
    """A flowable, or None when there's nobody to sign."""
    if user is None and not name:
        return None
    sig = _image(getattr(user, "signature_image", None), getattr(user, "signature_width_mm", 35)) if user else None
    stamp = _image(getattr(user, "stamp_image", None), getattr(user, "stamp_width_mm", 28)) if user else None
    reg = getattr(user, "registration_number", "") if user else ""
    display = name or (user.get_full_name() if user else "")
    style = ParagraphStyle("sig", fontSize=font_size, leading=font_size + 2, alignment={"LEFT": 0, "CENTER": 1, "RIGHT": 2}[align])
    text = Paragraph(f"<b>{display}</b>{f'<br/>{subtitle}' if subtitle else ''}{f'<br/>Reg. No. {reg}' if reg else ''}", style)
    images = [x for x in (stamp, sig) if x is not None]
    rows = [[*images]] if images else []
    if rows:
        img_row = Table(rows, hAlign=align)
        img_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
        block = Table([[img_row], [text]], hAlign=align)
    else:
        block = Table([[text]], hAlign=align)
    block.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), align), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return block
