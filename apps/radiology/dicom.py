"""Tiny, dependency-free DICOM Part-10 header reader — enough to pull the
Study/Series/SOP Instance UIDs and Modality out of an uploaded file so it
can be linked to its order and opened in a PACS viewer (NABH IMS.1.g). It
handles explicit-VR little-endian (the transfer syntax virtually every
modality writes for storage) and stops at pixel data; anything it can't
parse just yields no tags — the file is still stored."""
import struct

TAGS = {
    (0x0020, 0x000D): "study_instance_uid",
    (0x0020, 0x000E): "series_instance_uid",
    (0x0008, 0x0018): "sop_instance_uid",
    (0x0008, 0x0060): "modality",
    (0x0010, 0x0020): "patient_id",
    (0x0008, 0x0050): "accession_number",
}
_LONG_VRS = {b"OB", b"OW", b"OF", b"SQ", b"UT", b"UN", b"OD", b"OL", b"UC", b"UR", b"OV", b"SV", b"UV"}


def is_dicom(head: bytes) -> bool:
    return len(head) >= 132 and head[128:132] == b"DICM"


def read_tags(data: bytes) -> dict:
    out = {}
    if not is_dicom(data):
        return out
    i, n = 132, len(data)
    while i + 8 <= n and len(out) < len(TAGS):
        group, elem = struct.unpack_from("<HH", data, i)
        if (group, elem) == (0x7FE0, 0x0010):
            break
        vr = data[i + 4:i + 6]
        if vr in _LONG_VRS:
            if i + 12 > n:
                break
            length = struct.unpack_from("<I", data, i + 8)[0]
            start = i + 12
        elif vr.isalpha():
            length = struct.unpack_from("<H", data, i + 6)[0]
            start = i + 8
        else:  # implicit VR
            length = struct.unpack_from("<I", data, i + 4)[0]
            start = i + 8
        if length == 0xFFFFFFFF:  # undefined-length sequence — skip rest safely
            break
        key = TAGS.get((group, elem))
        if key:
            out[key] = data[start:start + length].decode("ascii", errors="ignore").strip("\x00 ").strip()
        i = start + length
    return out
