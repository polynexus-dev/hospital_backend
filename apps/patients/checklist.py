"""Required-documents checklist: what a patient still has to give us at a
stage of care, measured against the hospital's DocumentRequirement list.

An item is met by an upload linked to it, or (reuse_same_category) by any
upload of the same category, e.g. one Aadhaar scan covers ID proof at
registration, admission and surgery. valid_days makes an item need a recent
upload (consents). only_if_insured items apply only to patients with an
insurer on record.
"""
from datetime import timedelta

from django.utils import timezone

from .models import Document, DocumentRequirement

S, C = DocumentRequirement.Stage, Document.Category

# (stage, name, category, required, only_if_insured, valid_days, help)
DEFAULTS = [
    (S.REGISTRATION, "Photo ID (Aadhaar, PAN, passport, voter ID or driving licence)", C.ID_PROOF, True, False, None, ""),
    (S.REGISTRATION, "Insurance / TPA card", C.INSURANCE_CARD, True, True, None, "Front and back"),
    (S.ADMISSION, "Photo ID", C.ID_PROOF, True, False, None, ""),
    (S.ADMISSION, "Insurance / TPA card", C.INSURANCE_CARD, True, True, None, ""),
    (S.ADMISSION, "Signed general consent for admission", C.CONSENT, True, False, 30, "Signed at this admission"),
    (S.ADMISSION, "Previous medical records", C.REPORT, False, False, None, "Discharge summaries, reports"),
    (S.SURGERY, "Signed surgical consent", C.CONSENT, True, False, 30, ""),
    (S.SURGERY, "Pre-anaesthesia check report", C.REPORT, True, False, 30, ""),
    (S.RADIOLOGY, "Doctor's requisition / prescription", C.PRESCRIPTION, True, False, 90, ""),
    (S.RADIOLOGY, "Previous imaging reports", C.REPORT, False, False, None, "For comparison"),
    (S.LABORATORY, "Doctor's prescription", C.PRESCRIPTION, False, False, 90, ""),
    (S.INSURANCE, "Insurance / TPA card", C.INSURANCE_CARD, True, False, None, ""),
    (S.INSURANCE, "Photo ID", C.ID_PROOF, True, False, None, ""),
]


def ensure_defaults(hospital):
    if DocumentRequirement.objects.filter(hospital=hospital).exists():
        return
    DocumentRequirement.objects.bulk_create([
        DocumentRequirement(hospital=hospital, stage=st, name=n, category=c, required=r, only_if_insured=ins, valid_days=days, help_text=h, order=i * 10,
                            reuse_same_category=c in (C.ID_PROOF, C.INSURANCE_CARD))
        for i, (st, n, c, r, ins, days, h) in enumerate(DEFAULTS)
    ])


def _insured(patient):
    return bool((patient.insurance_provider or "").strip() or (patient.insurance_policy_number or "").strip())


def checklist(patient, stage):
    ensure_defaults(patient.hospital)
    reqs = DocumentRequirement.objects.filter(hospital_id=patient.hospital_id, stage=stage, is_active=True)
    docs = list(Document.objects.filter(patient=patient).order_by("-created_at"))
    insured, now, items = _insured(patient), timezone.now(), []
    for r in reqs:
        if r.only_if_insured and not insured:
            continue
        fresh = [d for d in docs if r.valid_days is None or d.created_at >= now - timedelta(days=r.valid_days)]
        match = next((d for d in fresh if d.requirement_id == r.pk), None)
        if match is None and r.reuse_same_category and r.category != C.OTHER:
            match = next((d for d in fresh if d.category == r.category), None)
        items.append({
            "requirement": r.pk, "name": r.name, "category": r.category, "required": r.required, "help_text": r.help_text,
            "valid_days": r.valid_days, "satisfied": match is not None,
            "document": {"id": match.pk, "title": match.title or match.file.name.rsplit("/", 1)[-1], "uploaded_at": match.created_at,
                         "via_portal": match.uploaded_via_portal} if match else None,
        })
    missing = [i for i in items if i["required"] and not i["satisfied"]]
    return {"stage": stage, "stage_label": S(stage).label, "items": items, "missing_required": len(missing),
            "complete": not missing, "missing_names": [i["name"] for i in missing]}
