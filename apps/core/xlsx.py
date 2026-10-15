"""Minimal, dependency-free .xlsx writer (one sheet, strings/numbers only)
— enough for report exports (NABH KPIs, GST, MIS) without pulling in
openpyxl."""
import io
import zipfile
from xml.sax.saxutils import escape


def _col(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def build_xlsx(rows, sheet_name="Sheet1"):
    sheet_rows = []
    for r_idx, row in enumerate(rows, start=1):
        cells = []
        for c_idx, value in enumerate(row, start=1):
            ref = f"{_col(c_idx)}{r_idx}"
            if value is None or value == "":
                continue
            if isinstance(value, bool):
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>')
            elif isinstance(value, (int, float)):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>')
        sheet_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{escape(sheet_name[:31])}" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>"
        ),
        "xl/worksheets/sheet1.xml": sheet,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


# --- reading -----------------------------------------------------------------------

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
MAX_IMPORT_ROWS = 10_000
MAX_UNZIPPED_BYTES = 50 * 1024 * 1024


class SpreadsheetError(ValueError):
    pass


def _col_index(ref):
    letters = "".join(ch for ch in ref if ch.isalpha())
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def read_xlsx(data: bytes, max_rows=MAX_IMPORT_ROWS):
    """Rows of the first sheet as lists of strings (no openpyxl). Handles
    shared strings, inline strings, numbers and booleans; formulas give
    their cached value. Raises SpreadsheetError on anything that isn't a
    readable .xlsx."""
    from xml.etree import ElementTree as ET

    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SpreadsheetError("Not an .xlsx file.") from exc
    if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED_BYTES:
        raise SpreadsheetError("The file is too large once unpacked.")
    names = set(z.namelist())

    def part(name):
        # Uploaded XML: a real workbook never declares a DTD or entities, so
        # refuse any that does (no entity-expansion or external-entity tricks).
        raw = z.read(name)
        if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
            raise SpreadsheetError("The file contains unsupported XML declarations.")
        return ET.fromstring(raw)

    shared = []
    if "xl/sharedStrings.xml" in names:
        for si in part("xl/sharedStrings.xml").iter(f"{_NS}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))
    sheet = "xl/worksheets/sheet1.xml"
    if "xl/workbook.xml" in names and "xl/_rels/workbook.xml.rels" in names:
        first = part("xl/workbook.xml").find(f"{_NS}sheets/{_NS}sheet")
        rels = part("xl/_rels/workbook.xml.rels")
        if first is not None:
            rid = first.get(f"{_REL_NS}id")
            for rel in rels:
                if rel.get("Id") == rid:
                    target = rel.get("Target", "").lstrip("/")
                    sheet = target if target.startswith("xl/") else f"xl/{target}"
    if sheet not in names:
        raise SpreadsheetError("The workbook has no worksheet.")
    rows = []
    for row in part(sheet).iter(f"{_NS}row"):
        if len(rows) >= max_rows:
            raise SpreadsheetError(f"More than {max_rows} rows; split the file.")
        values = {}
        for c in row.iter(f"{_NS}c"):
            t, ref = c.get("t", "n"), c.get("r", "")
            if t == "inlineStr":
                text = "".join(x.text or "" for x in c.iter(f"{_NS}t"))
            else:
                v = c.find(f"{_NS}v")
                raw = v.text if v is not None and v.text is not None else ""
                text = shared[int(raw)] if t == "s" and raw.isdigit() and int(raw) < len(shared) else ("TRUE" if raw == "1" else "FALSE") if t == "b" else raw
                if t == "n" and text.endswith(".0"):
                    text = text[:-2]
            values[_col_index(ref) if ref else len(values)] = text.strip()
        if values:
            rows.append([values.get(i, "") for i in range(max(values) + 1)])
    return rows


def read_table(upload):
    """Rows from an uploaded .xlsx or .csv file."""
    import csv

    data = upload.read()
    name = (getattr(upload, "name", "") or "").lower()
    if name.endswith(".csv"):
        text = data.decode("utf-8-sig", errors="replace")
        return [[c.strip() for c in r] for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)][:MAX_IMPORT_ROWS]
    return read_xlsx(data)
