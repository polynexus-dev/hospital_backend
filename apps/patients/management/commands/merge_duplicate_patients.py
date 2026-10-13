"""Merge exact duplicate patient records: same hospital, same mobile, same
name and no conflicting date of birth. The oldest record (lowest UHID) is
kept; visits, bills and everything else move to it and the copies are
soft-deleted with an audit reason (see registration.merge_patients).

    python manage.py merge_duplicate_patients            # dry run, lists groups
    python manage.py merge_duplicate_patients --apply
"""
from collections import defaultdict

from django.core.management.base import BaseCommand

from apps.patients.models import Patient
from apps.patients.registration import merge_patients


def duplicate_groups(queryset):
    buckets = defaultdict(list)
    for p in queryset.exclude(mobile_hash="").order_by("pk"):
        key = (p.hospital_id, p.mobile_hash, " ".join(f"{p.first_name} {p.last_name}".casefold().split()))
        buckets[key].append(p)
    for patients in buckets.values():
        if len(patients) < 2:
            continue
        dobs = {p.date_of_birth for p in patients if p.date_of_birth}
        if len(dobs) <= 1:  # different birth dates means different people sharing a phone
            yield patients


class Command(BaseCommand):
    help = "Merge exact duplicate patients (same hospital, mobile and name)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Merge; without it, only list what would be merged.")
        parser.add_argument("--hospital", type=int, help="Limit to one hospital id.")

    def handle(self, apply=False, hospital=None, **options):
        qs = Patient.objects.all()
        if hospital:
            qs = qs.filter(hospital_id=hospital)
        groups = merged = 0
        for patients in duplicate_groups(qs):
            primary, copies = patients[0], patients[1:]
            groups += 1
            self.stdout.write(f"{primary.full_name} ({primary.uhid}) <- {', '.join(p.uhid for p in copies)}")
            if apply:
                for dup in copies:
                    merge_patients(primary, dup)
                    merged += 1
        if apply:
            self.stdout.write(self.style.SUCCESS(f"Merged {merged} duplicate(s) in {groups} group(s)."))
        else:
            self.stdout.write(self.style.SUCCESS(f"{groups} group(s) found. Re-run with --apply to merge."))
