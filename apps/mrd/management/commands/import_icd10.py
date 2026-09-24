import csv

from django.core.management.base import BaseCommand

from apps.mrd.models import ICD10Code


class Command(BaseCommand):
    help = "Import ICD-10 codes from a CSV with columns: code,title[,chapter]"

    def add_arguments(self, parser):
        parser.add_argument("path")

    def handle(self, path, **opts):
        created = updated = 0
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                code = (row.get("code") or "").strip()
                if not code:
                    continue
                _, was_created = ICD10Code.objects.update_or_create(code=code, defaults={"title": row.get("title", "").strip()[:300], "chapter": row.get("chapter", "").strip()[:120]})
                created += was_created
                updated += not was_created
        self.stdout.write(self.style.SUCCESS(f"ICD-10: {created} created, {updated} updated"))
