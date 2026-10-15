"""The repository's base seed_demo_data uses a few realistic-looking 10-digit
mobiles (98xxxxxxxx). Map every phone number in the demo hospital that is not
already in the fictional +91 90000 series onto that series (consistently, so
the same caller keeps the same number everywhere). Hospital landlines in the
+91 20 67000000 demo series are left alone."""
import re

from django.apps import apps
from django.db import models

from apps.core.models import Hospital

PHONE_FIELD = re.compile(r"(phone|mobile|number|whatsapp|contact)", re.I)
REAL_LOOKING = re.compile(r"^(\+?91[- ]?)?[6-9]\d{9}$")
LANDLINE = re.compile(r"^0\d{2,4}[- ]?\d{6,8}$")  # e.g. 020-2xxxxxxx -> the demo hospital line
HOSPITAL_LINE = "+912067000000"


def _is_demo(v):
    d = re.sub(r"\D", "", v)
    return d[-10:].startswith("9000")


def run(ctx):
    h = ctx.get("h") or Hospital.objects.get(slug="demo-hospital")
    mapping, seq = {}, iter(range(90001, 99999))

    def demo_for(v):
        key = re.sub(r"\D", "", v)[-10:]
        if key not in mapping:
            mapping[key] = f"+9190000{next(seq):05d}"
        return mapping[key]

    changed = 0
    for model in apps.get_models():
        fields = [f for f in model._meta.concrete_fields
                  if PHONE_FIELD.search(f.name) and isinstance(f, (models.CharField, models.TextField)) and not f.name.endswith("_hash")]
        if not fields or not any(f.name == "hospital" for f in model._meta.concrete_fields):
            continue
        for obj in model.objects.filter(hospital=h):
            dirty = []
            for f in fields:
                v = getattr(obj, f.name) or ""
                if isinstance(v, str) and REAL_LOOKING.match(v.strip()) and not _is_demo(v):
                    setattr(obj, f.name, demo_for(v.strip()))
                    dirty.append(f.name)
                elif isinstance(v, str) and LANDLINE.match(v.strip()):
                    setattr(obj, f.name, HOSPITAL_LINE)
                    dirty.append(f.name)
            if dirty:
                hashes = [f.name for f in model._meta.concrete_fields if f.name.endswith("_hash")]
                obj.save(update_fields=dirty + hashes)  # the model's save() recomputes blind-index hashes
                changed += 1
    return f"{changed} records, {len(mapping)} numbers remapped"
