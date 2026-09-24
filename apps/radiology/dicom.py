"""Dependency-light DICOM Part-10 reader and renderer (NABH IMS.1.g).

* `read_tags(data)` — the UIDs / modality used to link a file to its order.
* `parse(data)`     — full dataset walk (implicit / explicit VR, little / big
  endian, nested and undefined-length sequences), returning the image
  attributes and the pixel data.
* `render_png(data, frame, center, width, invert)` — one frame as PNG with
  rescale slope/intercept and window/level applied, for the built-in viewer.

Pixel formats: uncompressed (the common storage syntaxes) via numpy, and
encapsulated JPEG baseline/extended and JPEG 2000 via Pillow. JPEG-lossless
and RLE are reported as unsupported — open those in the external PACS viewer.
"""
import io
import struct

UIDS = {
    (0x0020, 0x000D): "study_instance_uid",
    (0x0020, 0x000E): "series_instance_uid",
    (0x0008, 0x0018): "sop_instance_uid",
    (0x0008, 0x0060): "modality",
    (0x0010, 0x0020): "patient_id",
    (0x0008, 0x0050): "accession_number",
}
IMAGE_TAGS = {
    (0x0002, 0x0010): "transfer_syntax",
    (0x0028, 0x0002): "samples_per_pixel",
    (0x0028, 0x0004): "photometric",
    (0x0028, 0x0006): "planar_configuration",
    (0x0028, 0x0008): "frames",
    (0x0028, 0x0010): "rows",
    (0x0028, 0x0011): "columns",
    (0x0028, 0x0030): "pixel_spacing",
    (0x0028, 0x0100): "bits_allocated",
    (0x0028, 0x0101): "bits_stored",
    (0x0028, 0x0103): "pixel_representation",
    (0x0028, 0x1050): "window_center",
    (0x0028, 0x1051): "window_width",
    (0x0028, 0x1052): "rescale_intercept",
    (0x0028, 0x1053): "rescale_slope",
    (0x0020, 0x0013): "instance_number",
    (0x0020, 0x1041): "slice_location",
    (0x0008, 0x103E): "series_description",
    (0x0020, 0x0011): "series_number",
    **UIDS,
}
PIXEL_DATA = (0x7FE0, 0x0010)
IMPLICIT_LE = "1.2.840.10008.1.2"
EXPLICIT_BE = "1.2.840.10008.1.2.2"
UNCOMPRESSED = {IMPLICIT_LE, "1.2.840.10008.1.2.1", EXPLICIT_BE}
PILLOW_SYNTAXES = {"1.2.840.10008.1.2.4.50", "1.2.840.10008.1.2.4.51", "1.2.840.10008.1.2.4.90", "1.2.840.10008.1.2.4.91"}
_LONG_VRS = {b"OB", b"OW", b"OF", b"SQ", b"UT", b"UN", b"OD", b"OL", b"UC", b"UR", b"OV", b"SV", b"UV"}
_US_TAGS = {(0x0028, 0x0002), (0x0028, 0x0006), (0x0028, 0x0010), (0x0028, 0x0011), (0x0028, 0x0100), (0x0028, 0x0101), (0x0028, 0x0103)}
MAX_FRAMES = 2000


class DicomError(Exception):
    pass


def is_dicom(head: bytes) -> bool:
    return len(head) >= 132 and head[128:132] == b"DICM"


class _Reader:
    def __init__(self, data):
        self.data = data
        self.out = {}
        self.pixel = None  # (offset, length) or ("encapsulated", [fragments])

    def element(self, i, explicit, endian):
        d = self.data
        group, elem = struct.unpack_from(endian + "HH", d, i)
        tag = (group, elem)
        if group == 0xFFFE:  # item / delimiters are always implicit
            return tag, None, struct.unpack_from(endian + "I", d, i + 4)[0], i + 8
        if explicit:
            vr = d[i + 4:i + 6]
            if vr in _LONG_VRS:
                return tag, vr, struct.unpack_from(endian + "I", d, i + 8)[0], i + 12
            return tag, vr, struct.unpack_from(endian + "H", d, i + 6)[0], i + 8
        return tag, None, struct.unpack_from(endian + "I", d, i + 4)[0], i + 8

    def walk(self, i, end, explicit, endian, depth=0, stop_at_item_end=False):
        """Returns the index after the last element read."""
        d = self.data
        while i + 8 <= end:
            tag, vr, length, start = self.element(i, explicit, endian)
            if tag == (0xFFFE, 0xE00D) and stop_at_item_end:  # item delimiter
                return start
            if tag == PIXEL_DATA and depth == 0:
                if length == 0xFFFFFFFF:
                    self.pixel = ("encapsulated", self._fragments(start, endian))
                else:
                    self.pixel = (start, length)
                return end
            if vr == b"SQ" or (length == 0xFFFFFFFF):
                i = self._skip_sequence(start, length, explicit, endian, depth)
                continue
            if depth == 0 and tag in IMAGE_TAGS and start + length <= len(d):
                raw = d[start:start + length]
                if tag in _US_TAGS and length >= 2 and (vr in (None, b"US")):
                    self.out[IMAGE_TAGS[tag]] = struct.unpack_from(endian + "H", raw)[0]
                else:
                    self.out[IMAGE_TAGS[tag]] = raw.decode("ascii", errors="ignore").strip("\x00 ").strip()
            i = start + length
        return i

    def _skip_sequence(self, start, length, explicit, endian, depth):
        if length != 0xFFFFFFFF:
            return start + length
        i = start
        d = self.data
        while i + 8 <= len(d):
            tag, _vr, item_len, item_start = self.element(i, explicit, endian)
            if tag == (0xFFFE, 0xE0DD):  # sequence delimiter
                return item_start
            if tag != (0xFFFE, 0xE000):
                raise DicomError("Malformed sequence")
            if item_len == 0xFFFFFFFF:
                i = self.walk(item_start, len(d), explicit, endian, depth + 1, stop_at_item_end=True)
            else:
                i = item_start + item_len
        raise DicomError("Unterminated sequence")

    def _fragments(self, i, endian):
        d, frags, first = self.data, [], True
        while i + 8 <= len(d):
            (group, elem), length = struct.unpack_from(endian + "HH", d, i), struct.unpack_from(endian + "I", d, i + 4)[0]
            if (group, elem) == (0xFFFE, 0xE0DD):
                break
            if not first:  # first item is the basic offset table
                frags.append(d[i + 8:i + 8 + length])
            first = False
            i += 8 + length
        return frags


def parse(data: bytes, partial=False) -> dict:
    """Image attributes plus `_pixel` (internal). Raises DicomError if unreadable;
    with `partial=True` (a truncated upload head) it keeps whatever it read."""
    if not is_dicom(data):
        raise DicomError("Not a DICOM Part-10 file")
    r = _Reader(data)
    # File meta (group 0002) is always explicit VR little endian.
    i = 132
    while i + 8 <= len(data):
        group = struct.unpack_from("<H", data, i)[0]
        if group != 0x0002:
            break
        tag, vr, length, start = r.element(i, True, "<")
        if tag == (0x0002, 0x0010):
            r.out["transfer_syntax"] = data[start:start + length].decode("ascii", errors="ignore").strip("\x00 ")
        i = start + length
    ts = r.out.get("transfer_syntax", "1.2.840.10008.1.2.1")
    explicit, endian = ts != IMPLICIT_LE, (">" if ts == EXPLICIT_BE else "<")
    try:
        r.walk(i, len(data), explicit, endian)
    except (struct.error, DicomError) as e:
        if not partial:
            raise DicomError(f"Could not read the dataset: {e}") from e
    info = dict(r.out)
    info["frames"] = int(str(info.get("frames") or 1).split("\\")[0] or 1)
    info["_pixel"], info["_endian"] = r.pixel, endian
    return info


def read_tags(data: bytes) -> dict:
    """The UIDs / modality only (kept for the upload path)."""
    try:
        info = parse(data, partial=True)
    except DicomError:
        return {}
    return {k: info[k] for k in UIDS.values() if info.get(k)}


def _first_number(value, default=None):
    try:
        return float(str(value).split("\\")[0])
    except (TypeError, ValueError):
        return default


def describe(info: dict) -> dict:
    """What the viewer needs to know about an image."""
    ts = info.get("transfer_syntax", "")
    supported = ts in UNCOMPRESSED or ts in PILLOW_SYNTAXES
    spacing = [_first_number(v) for v in str(info.get("pixel_spacing", "")).split("\\")] if info.get("pixel_spacing") else None
    return {
        "rows": info.get("rows"), "columns": info.get("columns"), "frames": info.get("frames", 1),
        "modality": info.get("modality", ""), "photometric": info.get("photometric", ""),
        "window_center": _first_number(info.get("window_center")), "window_width": _first_number(info.get("window_width")),
        "pixel_spacing_mm": spacing if spacing and all(spacing) else None,
        "instance_number": int(_first_number(info.get("instance_number"), 0) or 0),
        "slice_location": _first_number(info.get("slice_location")),
        "series_instance_uid": info.get("series_instance_uid", ""), "series_description": info.get("series_description", ""),
        "series_number": int(_first_number(info.get("series_number"), 0) or 0),
        "transfer_syntax": ts, "supported": supported and info.get("_pixel") is not None,
        "unsupported_reason": "" if supported else "Compressed with a codec the built-in viewer can't decode (e.g. JPEG-lossless / RLE) — use the PACS viewer.",
    }


def _frame_array(info, data, frame):
    import numpy as np
    from PIL import Image

    rows, cols = info.get("rows"), info.get("columns")
    frames = min(info.get("frames", 1), MAX_FRAMES)
    if not rows or not cols:
        raise DicomError("Image has no rows/columns")
    if not 0 <= frame < frames:
        raise DicomError(f"Frame {frame} out of range (0–{frames - 1})")
    pixel, ts = info.get("_pixel"), info.get("transfer_syntax", "")
    samples = int(info.get("samples_per_pixel") or 1)
    if pixel is None:
        raise DicomError("No pixel data")
    if isinstance(pixel[0], str):  # encapsulated
        if ts not in PILLOW_SYNTAXES:
            raise DicomError("Unsupported compression")
        frags = pixel[1]
        chunk = frags[frame] if len(frags) >= frames else b"".join(frags)  # one fragment per frame, or one frame in many
        img = Image.open(io.BytesIO(chunk))
        return np.asarray(img), samples > 1 or img.mode in ("RGB", "YCbCr")
    if ts not in UNCOMPRESSED:
        raise DicomError("Unsupported transfer syntax")
    bits = int(info.get("bits_allocated") or 16)
    signed = int(info.get("pixel_representation") or 0) == 1
    dtype = {8: "i1" if signed else "u1", 16: "i2" if signed else "u2", 32: "i4" if signed else "u4"}.get(bits)
    if dtype is None:
        raise DicomError(f"{bits}-bit pixels are not supported")
    per_frame = rows * cols * samples * (bits // 8)
    offset = pixel[0] + frame * per_frame
    arr = np.frombuffer(data, dtype=(info["_endian"] + dtype) if bits > 8 else dtype, count=rows * cols * samples, offset=offset)
    if samples > 1:
        arr = arr.reshape(samples, rows, cols).transpose(1, 2, 0) if int(info.get("planar_configuration") or 0) == 1 else arr.reshape(rows, cols, samples)
        return arr, True
    return arr.reshape(rows, cols), False


def render_png(data: bytes, frame=0, center=None, width=None, invert=False) -> bytes:
    import numpy as np
    from PIL import Image

    info = parse(data)
    arr, colour = _frame_array(info, data, frame)
    if colour:
        out = arr.astype(np.uint8)
        img = Image.fromarray(out[..., :3] if out.ndim == 3 else out)
    else:
        slope = _first_number(info.get("rescale_slope"), 1.0) or 1.0
        intercept = _first_number(info.get("rescale_intercept"), 0.0) or 0.0
        values = arr.astype(np.float32) * slope + intercept
        c = center if center is not None else _first_number(info.get("window_center"))
        w = width if width is not None else _first_number(info.get("window_width"))
        if c is None or not w:
            lo, hi = float(values.min()), float(values.max())
            c, w = (lo + hi) / 2, max(hi - lo, 1.0)
        lo = c - w / 2
        scaled = np.clip((values - lo) / w, 0, 1) * 255
        if (info.get("photometric") == "MONOCHROME1") != bool(invert):
            scaled = 255 - scaled
        img = Image.fromarray(np.rint(scaled).astype(np.uint8), mode="L")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()
