import io
import struct

import numpy as np
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.radiology import dicom

EXPLICIT = "1.2.840.10008.1.2.1"
IMPLICIT = "1.2.840.10008.1.2"
JPEG = "1.2.840.10008.1.2.4.50"
JPEG_LOSSLESS = "1.2.840.10008.1.2.4.70"


def _pad(v):
    return v + (b"\x00" if len(v) % 2 else b"")


def build(ts=EXPLICIT, rows=2, cols=2, pixels=None, bits=16, signed=True, frames=1, photometric="MONOCHROME2",
          wc=None, ww=None, intercept=None, extra=b"", encapsulated=None):
    explicit = ts != IMPLICIT

    def el(g, e, vr, val):
        val = _pad(val)
        if explicit or g == 0x0002:
            if vr in (b"OB", b"OW", b"SQ"):
                return struct.pack("<HH", g, e) + vr + b"\x00\x00" + struct.pack("<I", len(val)) + val
            return struct.pack("<HH", g, e) + vr + struct.pack("<H", len(val)) + val
        return struct.pack("<HHI", g, e, len(val)) + val

    us = lambda v: struct.pack("<H", v)  # noqa: E731
    meta = el(0x0002, 0x0010, b"UI", ts.encode())
    body = el(0x0008, 0x0060, b"CS", b"CT") + el(0x0020, 0x000D, b"UI", b"1.2.3") + el(0x0020, 0x000E, b"UI", b"1.2.3.4")
    body += el(0x0020, 0x0013, b"IS", b"7") + extra
    body += el(0x0028, 0x0002, b"US", us(1)) + el(0x0028, 0x0004, b"CS", photometric.encode())
    if frames > 1:
        body += el(0x0028, 0x0008, b"IS", str(frames).encode())
    body += el(0x0028, 0x0010, b"US", us(rows)) + el(0x0028, 0x0011, b"US", us(cols)) + el(0x0028, 0x0030, b"DS", b"0.5\\0.5")
    body += el(0x0028, 0x0100, b"US", us(bits)) + el(0x0028, 0x0101, b"US", us(bits)) + el(0x0028, 0x0103, b"US", us(1 if signed else 0))
    if wc is not None:
        body += el(0x0028, 0x1050, b"DS", str(wc).encode()) + el(0x0028, 0x1051, b"DS", str(ww).encode())
    if intercept is not None:
        body += el(0x0028, 0x1052, b"DS", str(intercept).encode()) + el(0x0028, 0x1053, b"DS", b"1")
    if encapsulated is not None:
        frags = struct.pack("<HHI", 0xFFFE, 0xE000, 0)  # empty basic offset table
        for f in encapsulated:
            frags += struct.pack("<HHI", 0xFFFE, 0xE000, len(_pad(f))) + _pad(f)
        frags += struct.pack("<HHI", 0xFFFE, 0xE0DD, 0)
        body += struct.pack("<HH", 0x7FE0, 0x0010) + b"OB\x00\x00" + struct.pack("<I", 0xFFFFFFFF) + frags
    else:
        body += el(0x7FE0, 0x0010, b"OW", pixels.tobytes())
    return b"\x00" * 128 + b"DICM" + meta + body


def png_pixels(png):
    return np.asarray(Image.open(io.BytesIO(png)))


def test_ct_rescale_window_and_monochrome1():
    raw = np.array([[0, 1024], [1064, 2048]], dtype="<i2")  # with intercept -1024 → -1024, 0, 40, 1024 HU
    data = build(pixels=raw, intercept=-1024, wc=40, ww=80)
    info = dicom.parse(data)
    assert dicom.describe(info)["pixel_spacing_mm"] == [0.5, 0.5] and dicom.describe(info)["instance_number"] == 7
    out = png_pixels(dicom.render_png(data))  # window 0..80 HU
    assert out.tolist() == [[0, 0], [128, 255]]
    assert png_pixels(dicom.render_png(data, center=0, width=2048)).tolist()[0][0] == 0  # -1024 → bottom of window
    assert png_pixels(dicom.render_png(data, invert=True)).tolist() == [[255, 255], [128, 0]]
    mono1 = build(pixels=raw, intercept=-1024, wc=40, ww=80, photometric="MONOCHROME1")
    assert png_pixels(dicom.render_png(mono1)).tolist() == [[255, 255], [128, 0]]


def test_implicit_vr_multiframe_and_nested_sequence():
    seq_item = struct.pack("<HHI", 0x0008, 0x0100, 4) + b"T-01"  # implicit element inside the item
    sequence = (struct.pack("<HHI", 0x0008, 0x1140, 0xFFFFFFFF) + struct.pack("<HHI", 0xFFFE, 0xE000, 0xFFFFFFFF) + seq_item
                + struct.pack("<HHI", 0xFFFE, 0xE00D, 0) + struct.pack("<HHI", 0xFFFE, 0xE0DD, 0))
    frames = np.array([[[0, 100], [200, 255]], [[255, 200], [100, 0]]], dtype="u1")
    data = build(ts=IMPLICIT, pixels=frames, bits=8, signed=False, frames=2, extra=sequence)
    info = dicom.parse(data)
    assert info["frames"] == 2 and info["rows"] == 2
    assert png_pixels(dicom.render_png(data, frame=1)).tolist() == [[255, 200], [100, 0]]
    with pytest.raises(dicom.DicomError, match="out of range"):
        dicom.render_png(data, frame=2)


def test_jpeg_encapsulated_and_unsupported_codec():
    buf = io.BytesIO()
    Image.fromarray(np.full((8, 8), 200, dtype="u1"), mode="L").save(buf, format="JPEG", quality=95)
    data = build(ts=JPEG, rows=8, cols=8, bits=8, signed=False, encapsulated=[buf.getvalue()])
    assert dicom.describe(dicom.parse(data))["supported"] is True
    assert abs(int(png_pixels(dicom.render_png(data, center=128, width=256)).mean()) - 200) <= 3

    lossless = build(ts=JPEG_LOSSLESS, rows=8, cols=8, bits=8, signed=False, encapsulated=[b"\xff\xd8junk"])
    assert dicom.describe(dicom.parse(lossless))["supported"] is False
    with pytest.raises(dicom.DicomError):
        dicom.render_png(lossless)


def test_truncated_upload_head_still_yields_uids():
    data = build(pixels=np.zeros((64, 64), dtype="<i2"))
    assert dicom.read_tags(data[:220])["study_instance_uid"] == "1.2.3"


@pytest.mark.django_db
def test_viewer_api_groups_series_and_renders(auth_client, hospital, tmp_path, settings):
    from apps.patients.models import Patient
    from apps.radiology.models import RadiologyProcedure

    settings.MEDIA_ROOT = tmp_path
    patient = Patient.objects.create(hospital=hospital, first_name="Img", mobile="9876500061")
    ct = RadiologyProcedure.objects.create(hospital=hospital, name="CT head", modality="ct")
    order = auth_client.post("/api/v1/radiology/orders/", {"patient": patient.pk, "procedure": ct.pk}, format="json").json()
    raw = np.array([[0, 1024], [1064, 2048]], dtype="<i2")
    files = [SimpleUploadedFile("a.dcm", build(pixels=raw, intercept=-1024, wc=40, ww=80)),
             SimpleUploadedFile("key.jpg", b"\xff\xd8\xff\xe0 not really", content_type="image/jpeg")]
    up = auth_client.post(f"/api/v1/radiology/orders/{order['id']}/upload_images/", {"files": files}, format="multipart")
    assert up.status_code == 200 and up.json()["study_instance_uid"] == "1.2.3"

    study = auth_client.get(f"/api/v1/radiology/orders/{order['id']}/viewer/").json()
    dicom_series = next(s for s in study["series"] if s["series_instance_uid"] == "1.2.3.4")
    img = dicom_series["images"][0]
    assert img["supported"] and img["window_center"] == 40.0 and img["rows"] == 2

    png = auth_client.get(f"/api/v1/radiology/orders/{order['id']}/images/{img['id']}/render/", {"wc": 40, "ww": 80})
    assert png.status_code == 200 and png["Content-Type"] == "image/png"
    assert png_pixels(png.content).tolist() == [[0, 0], [128, 255]]
    assert auth_client.get(f"/api/v1/radiology/orders/{order['id']}/images/{img['id']}/render/", {"frame": 3}).status_code == 422
    assert auth_client.get(f"/api/v1/radiology/orders/{order['id']}/images/{img['id']}/render/", {"wc": "abc"}).status_code == 400
