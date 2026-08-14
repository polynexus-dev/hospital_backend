"""Data migration: assign a uhid to every Patient row that predates the
uhid field (migration 0009). New rows get one automatically in
Patient.save() (apps.patients.models.Patient._generate_uhid) — this only
covers the one-time backfill for existing data, using the same per-hospital
sequence counter (Hospital.next_uhid_sequence) so newly-created patients
after this migration don't collide with the numbers assigned here.
"""
from django.db import migrations, transaction


def backfill_uhid(apps, schema_editor):
    Patient = apps.get_model("patients", "Patient")
    Hospital = apps.get_model("core", "Hospital")

    for hospital in Hospital.objects.all():
        patients = list(Patient.objects.filter(hospital=hospital, uhid__isnull=True).order_by("created_at"))
        if not patients:
            continue
        with transaction.atomic():
            # select_for_update isn't available on a historical model via
            # this simple form on every backend consistently within a data
            # migration; this runs once, offline, ahead of any live
            # traffic reaching the new uhid column, so the race condition
            # TenantScopedModel.save() guards against in real usage isn't
            # a concern here.
            h_obj = Hospital.objects.get(pk=hospital.pk)
            current = getattr(h_obj, "next_uhid_sequence", 1) or 1
            for patient in patients:
                patient.uhid = f"{hospital.slug.upper()}-{current:06d}"
                patient.save(update_fields=["uhid"])
                current += 1
            if hasattr(h_obj, "next_uhid_sequence"):
                Hospital.objects.filter(pk=hospital.pk).update(next_uhid_sequence=current)


def noop_reverse(apps, schema_editor):
    pass  # uhid stays nullable; nothing to undo that would be meaningful


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_hospitalgroup_hospital_next_uhid_sequence_and_more"),
        ("patients", "0009_alter_patient_options_patient_blood_group_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_uhid, noop_reverse),
    ]
