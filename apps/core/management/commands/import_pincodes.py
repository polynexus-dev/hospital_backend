"""Load India Post's All-India Pincode Directory for PIN-code lookup.

The directory (data.gov.in, Government Open Data Licence - India) ships with
the product in apps/core/data/india_pincodes.csv.gz and is loaded on start-up
(`import_pincodes --if-empty`). To load a newer CSV from data.gov.in:

    python manage.py import_pincodes all_india_pincode_directory.csv

Safe to re-run (existing offices are kept). Column names are matched
case-insensitively: pincode, officename, district (or districtname),
statename, and taluk / sub-district when present.
"""
import csv
import gzip
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core.models import IndianState, PinCode

BUNDLED = Path(__file__).resolve().parents[2] / "data" / "india_pincodes.csv.gz"

ALIASES = {
    "the dadra and nagar haveli and daman and diu": "Dadra and Nagar Haveli and Daman and Diu",
    "dadra and nagar haveli": "Dadra and Nagar Haveli and Daman and Diu",
    "daman and diu": "Dadra and Nagar Haveli and Daman and Diu",
    "andaman and nicobar": "Andaman and Nicobar Islands",
    "andaman & nicobar islands": "Andaman and Nicobar Islands",
    "jammu & kashmir": "Jammu and Kashmir",
    "new delhi": "Delhi",
    "orissa": "Odisha",
    "pondicherry": "Puducherry",
    "chattisgarh": "Chhattisgarh",
    "uttaranchal": "Uttarakhand",
}


def tidy(value: str) -> str:
    return " ".join(w if w.isupper() and len(w) <= 3 else w.capitalize() for w in str(value or "").strip().split())


def column(row: dict, *names):
    for name in names:
        if name in row and row[name] not in (None, "", "NA"):
            return row[name]
    return ""


class Command(BaseCommand):
    help = "Load India Post's pincode directory (CSV from data.gov.in) for PIN-code lookup."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", nargs="?", default=str(BUNDLED), help="CSV or .csv.gz (default: the bundled directory)")
        parser.add_argument("--if-empty", action="store_true", help="Do nothing if PIN codes are already loaded (used on start-up).")

    def handle(self, csv_path, **options):
        if options["if_empty"] and PinCode.objects.exists():
            return
        states = {s.name.lower(): s.name for s in IndianState.objects.all()}
        try:
            opener = gzip.open if str(csv_path).endswith(".gz") else open
            handle = opener(csv_path, "rt", encoding="utf-8-sig", newline="")
        except OSError as exc:
            raise CommandError(f"Can't open {csv_path}: {exc.strerror}")
        batch, unknown_states, read = [], set(), 0
        with handle:
            for raw in csv.DictReader(handle):
                row = {k.strip().lower().replace(" ", "").replace("_", ""): (v or "").strip() for k, v in raw.items() if k}
                pin = column(row, "pincode", "pin")
                if not (len(pin) == 6 and pin.isdigit()):
                    continue
                state_raw = column(row, "statename", "state").lower()
                state = states.get(state_raw) or ALIASES.get(state_raw)
                if not state:
                    unknown_states.add(state_raw)
                    state = tidy(state_raw)
                batch.append(PinCode(
                    pincode=pin, office_name=tidy(column(row, "officename", "office"))[:120] or pin,
                    sub_district=tidy(column(row, "taluk", "subdistrict", "subdistname", "tehsil"))[:80],
                    district=tidy(column(row, "district", "districtname"))[:80], state=state[:80],
                ))
                read += 1
                if len(batch) >= 2000:
                    PinCode.objects.bulk_create(batch, ignore_conflicts=True)
                    batch = []
        PinCode.objects.bulk_create(batch, ignore_conflicts=True)
        self.stdout.write(self.style.SUCCESS(f"Read {read} post offices; {PinCode.objects.values('pincode').distinct().count()} PIN codes available."))
        if unknown_states:
            self.stdout.write(f"State names not in the list (kept as written): {', '.join(sorted(unknown_states))}")
