"""Load India Post's All-India Pincode Directory for PIN-code lookup.

The directory (data.gov.in, Government Open Data Licence - India) ships with
the product in apps/core/data/india_pincodes.csv.gz and is loaded on start-up
(`import_pincodes --if-empty`), so lookups work offline and no API key is
needed on hospital servers.

To refresh it when the Department of Posts publishes an update:

    python manage.py import_pincodes --from-api            # needs DATA_GOV_IN_API_KEY in .env
    python manage.py import_pincodes all_india_pincode_directory.csv

Safe to re-run: existing offices are kept, new ones added.
"""
import csv
import gzip
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.models import IndianState, PinCode

BUNDLED = Path(__file__).resolve().parents[2] / "data" / "india_pincodes.csv.gz"
API_RESOURCE = "04cbe4b1-2f2b-4c39-a1d5-1c2e28bc0e32"  # data.gov.in: All India Pincode directory
API_PAGE = 1000

ALIASES = {
    "the dadra and nagar haveli and daman and diu": "Dadra and Nagar Haveli and Daman and Diu",
    "dadra and nagar haveli": "Dadra and Nagar Haveli and Daman and Diu",
    "dadra & nagar haveli": "Dadra and Nagar Haveli and Daman and Diu",
    "daman and diu": "Dadra and Nagar Haveli and Daman and Diu",
    "daman & diu": "Dadra and Nagar Haveli and Daman and Diu",
    "andaman and nicobar": "Andaman and Nicobar Islands",
    "andaman & nicobar islands": "Andaman and Nicobar Islands",
    "jammu & kashmir": "Jammu and Kashmir",
    "new delhi": "Delhi",
    "orissa": "Odisha",
    "pondicherry": "Puducherry",
    "chattisgarh": "Chhattisgarh",
    "uttaranchal": "Uttarakhand",
}
LADAKH_DISTRICTS = {"leh", "kargil"}  # listed under Jammu and Kashmir before 2019
OFFICE_SUFFIX = re.compile(r"\s+(B\.?O|S\.?O|H\.?O|GPO|G\.P\.O)\.?$", re.I)


def tidy(value: str) -> str:
    words = " ".join(str(value or "").split()).lower().split()
    return re.sub(r"\b(And|Of|The)\b", lambda m: m.group(1).lower(), " ".join(w.capitalize() for w in words))


def column(row: dict, *names):
    for name in names:
        if name in row and row[name] not in (None, "", "NA", "NULL"):
            return row[name]
    return ""


def api_rows(api_key, out):
    """Every record of the data.gov.in resource, page by page."""
    offset, total = 0, None
    while total is None or offset < total:
        query = urllib.parse.urlencode({"api-key": api_key, "format": "json", "limit": API_PAGE, "offset": offset})
        try:
            with urllib.request.urlopen(f"https://api.data.gov.in/resource/{API_RESOURCE}?{query}", timeout=60) as resp:
                page = json.load(resp)
        except OSError as exc:
            raise CommandError(f"data.gov.in API request failed at offset {offset}: {exc}")
        if page.get("status") != "ok":
            raise CommandError(f"data.gov.in API: {page.get('message') or page}")
        total = int(page.get("total") or 0)
        records = page.get("records") or []
        if not records:
            break
        yield from records
        offset += len(records)
        out.write(f"\r  {offset}/{total} records", ending="")
    out.write("")


class Command(BaseCommand):
    help = "Load India Post's pincode directory for PIN-code lookup (bundled file, a CSV, or the data.gov.in API)."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", nargs="?", default=str(BUNDLED), help="CSV or .csv.gz (default: the bundled directory)")
        parser.add_argument("--if-empty", action="store_true", help="Do nothing if PIN codes are already loaded (used on start-up).")
        parser.add_argument("--from-api", action="store_true", help="Fetch the latest directory from data.gov.in (DATA_GOV_IN_API_KEY).")

    def handle(self, csv_path, **options):
        if options["if_empty"] and PinCode.objects.exists():
            return
        if options["from_api"]:
            api_key = getattr(settings, "DATA_GOV_IN_API_KEY", "")
            if not api_key:
                raise CommandError("Set DATA_GOV_IN_API_KEY in the server's .env to fetch from data.gov.in.")
            self._load(api_rows(api_key, self.stdout))
            return
        try:
            opener = gzip.open if str(csv_path).endswith(".gz") else open
            handle = opener(csv_path, "rt", encoding="utf-8-sig", newline="")
        except OSError as exc:
            raise CommandError(f"Can't open {csv_path}: {exc.strerror}")
        with handle:
            self._load(csv.DictReader(handle))

    def _load(self, rows):
        states = {s.name.lower(): s.name for s in IndianState.objects.all()}
        batch, unknown_states, read = [], set(), 0
        for raw in rows:
            row = {k.strip().lower().replace(" ", "").replace("_", ""): str(v or "").strip() for k, v in raw.items() if k}
            pin = column(row, "pincode", "pin")
            if not (len(pin) == 6 and pin.isdigit()):
                continue
            state_raw = column(row, "statename", "state").lower()
            if not state_raw:
                continue
            district = tidy(column(row, "district", "districtname"))
            state = states.get(state_raw) or ALIASES.get(state_raw)
            if not state:
                unknown_states.add(state_raw)
                state = tidy(state_raw)
            if state == "Jammu and Kashmir" and district.lower() in LADAKH_DISTRICTS:
                state = "Ladakh"
            office = OFFICE_SUFFIX.sub("", column(row, "officename", "office"))
            batch.append(PinCode(
                pincode=pin, office_name=tidy(office)[:120] or pin,
                sub_district=tidy(column(row, "taluk", "subdistrict", "subdistname", "tehsil"))[:80],
                district=district[:80], state=state[:80],
            ))
            read += 1
            if len(batch) >= 2000:
                PinCode.objects.bulk_create(batch, ignore_conflicts=True)
                batch = []
        PinCode.objects.bulk_create(batch, ignore_conflicts=True)
        self.stdout.write(self.style.SUCCESS(f"Read {read} post offices; {PinCode.objects.values('pincode').distinct().count()} PIN codes available."))
        if unknown_states:
            self.stdout.write(f"State names not in the list (kept as written): {', '.join(sorted(unknown_states))}")
