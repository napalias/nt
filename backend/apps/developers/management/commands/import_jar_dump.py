from __future__ import annotations

import csv
import json
import logging
import time
from pathlib import Path

import requests
from django.conf import settings
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand, CommandError

from apps.developers.models import Developer

logger = logging.getLogger(__name__)

RELEVANT_NACE_CODES = {"41.10", "41.20", "68.10", "68.20", "68.31"}

# CSV column name mappings — handles common JAR export column names.
# The actual JAR dump may use Lithuanian or English headers; we normalise here.
COLUMN_ALIASES = {
    "company_code": ["ja_kodas", "company_code", "code", "kodas", "juridinio_asmens_kodas"],
    "name": ["ja_pavadinimas", "name", "pavadinimas"],
    "nace_codes": ["evrk_kodas", "nace_codes", "nace", "evrk", "veiklos_kodas"],
    "registered_address": [
        "adresas",
        "registered_address",
        "address",
        "buveinės_adresas",
        "buveines_adresas",
    ],
    "founded": ["iregistravimo_data", "founded", "registration_date", "data"],
    "status": ["statusas", "status", "ja_statusas"],
    "employee_count": [
        "darbuotoju_skaicius",
        "employee_count",
        "employees",
        "darbuotojai",
    ],
}


def _resolve_columns(headers: list[str]) -> dict[str, str | None]:
    """Map our field names to actual column names found in the file."""
    header_lower = {h.lower().strip(): h for h in headers}
    mapping: dict[str, str | None] = {}
    for field, aliases in COLUMN_ALIASES.items():
        mapping[field] = None
        for alias in aliases:
            if alias.lower() in header_lower:
                mapping[field] = header_lower[alias.lower()]
                break
    return mapping


def _parse_nace_codes(raw: str) -> list[str]:
    """Parse NACE codes from various formats: comma-separated, semicolon-separated, or JSON."""
    if not raw:
        return []
    raw = raw.strip()
    if raw.startswith("["):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    # Try comma or semicolon separation
    for sep in [";", ","]:
        if sep in raw:
            return [c.strip() for c in raw.split(sep) if c.strip()]
    return [raw] if raw else []


def _has_relevant_nace(codes: list[str]) -> bool:
    """Check if any of the NACE codes match our relevant set."""
    return bool(RELEVANT_NACE_CODES & set(codes))


def _geocode_address(address: str, nominatim_url: str) -> Point | None:
    """Geocode an address via Nominatim. Returns Point or None."""
    if not address:
        return None
    try:
        resp = requests.get(
            f"{nominatim_url}/search",
            params={
                "q": address,
                "format": "json",
                "limit": 1,
                "countrycodes": "lt",
            },
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            return Point(
                float(results[0]["lon"]),
                float(results[0]["lat"]),
                srid=4326,
            )
    except (requests.RequestException, ValueError, KeyError, IndexError):
        logger.debug("Geocoding failed for address: %s", address)
    return None


def _parse_date(raw: str | None) -> str | None:
    """Try to parse a date string into YYYY-MM-DD. Returns None on failure."""
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    from datetime import datetime

    for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%Y.%m.%d"]:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _read_csv(filepath: Path) -> list[dict]:
    """Read a CSV file and return list of row dicts with resolved columns."""
    rows = []
    with open(filepath, encoding="utf-8-sig") as f:
        # Try to detect delimiter
        sample = f.read(4096)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        reader = csv.DictReader(f, dialect=dialect)
        if not reader.fieldnames:
            raise CommandError("CSV file has no headers")
        col_map = _resolve_columns(list(reader.fieldnames))
        if not col_map["company_code"]:
            raise CommandError(
                f"Cannot find company_code column. Headers found: {reader.fieldnames}"
            )
        if not col_map["name"]:
            raise CommandError(f"Cannot find name column. Headers found: {reader.fieldnames}")
        for row in reader:
            rows.append(_normalise_row(row, col_map))
    return rows


def _read_json(filepath: Path) -> list[dict]:
    """Read a JSON file (list of objects) and return normalised rows."""
    with open(filepath, encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise CommandError("JSON file must contain a list of objects")
    if not data:
        return []
    col_map = _resolve_columns(list(data[0].keys()))
    if not col_map["company_code"]:
        raise CommandError(f"Cannot find company_code field. Keys found: {list(data[0].keys())}")
    if not col_map["name"]:
        raise CommandError(f"Cannot find name field. Keys found: {list(data[0].keys())}")
    return [_normalise_row(row, col_map) for row in data]


def _normalise_row(row: dict, col_map: dict[str, str | None]) -> dict:
    """Extract and normalise fields from a raw row using the column mapping."""

    def get(field: str) -> str:
        col = col_map.get(field)
        if col is None:
            return ""
        val = row.get(col, "")
        return str(val).strip() if val else ""

    nace_raw = get("nace_codes")
    return {
        "company_code": get("company_code"),
        "name": get("name"),
        "nace_codes": _parse_nace_codes(nace_raw),
        "registered_address": get("registered_address"),
        "founded": get("founded"),
        "status": get("status") or "active",
        "employee_count": get("employee_count"),
    }


class Command(BaseCommand):
    help = "Import developers from a JAR open data dump (CSV or JSON)"

    def add_arguments(self, parser):
        parser.add_argument("filepath", type=str, help="Path to JAR data file (CSV or JSON)")
        parser.add_argument(
            "--no-geocode",
            action="store_true",
            help="Skip geocoding of registered addresses",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and filter but do not write to database",
        )

    def handle(self, *args, **options):
        filepath = Path(options["filepath"])
        if not filepath.is_file():
            raise CommandError(f"File not found: {filepath}")

        skip_geocode = options["no_geocode"]
        dry_run = options["dry_run"]
        nominatim_url = settings.NOMINATIM_URL

        # Parse file
        suffix = filepath.suffix.lower()
        if suffix == ".json":
            rows = _read_json(filepath)
        elif suffix in {".csv", ".tsv"}:
            rows = _read_csv(filepath)
        else:
            raise CommandError(f"Unsupported file format: {suffix}. Use .csv, .tsv, or .json")

        self.stdout.write(f"Parsed {len(rows)} total rows from {filepath.name}")

        # Filter by NACE codes
        filtered = [r for r in rows if _has_relevant_nace(r["nace_codes"])]
        self.stdout.write(
            f"Filtered to {len(filtered)} rows with relevant NACE codes "
            f"({', '.join(sorted(RELEVANT_NACE_CODES))})"
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — no records written"))
            return

        created = 0
        updated = 0
        skipped = 0

        for row in filtered:
            if not row["company_code"]:
                skipped += 1
                continue

            # Parse optional fields
            founded = _parse_date(row["founded"])
            employee_count = None
            if row["employee_count"]:
                try:
                    employee_count = int(row["employee_count"])
                except ValueError:
                    pass

            # Geocode if enabled
            point = None
            if not skip_geocode and row["registered_address"]:
                point = _geocode_address(row["registered_address"], nominatim_url)
                # Rate limit: 1 request per second (Nominatim usage policy)
                time.sleep(1)

            defaults = {
                "name": row["name"],
                "nace_codes": row["nace_codes"],
                "registered_address": row["registered_address"],
                "status": row["status"],
            }
            if founded:
                defaults["founded"] = founded
            if employee_count is not None:
                defaults["employee_count"] = employee_count
            if point is not None:
                defaults["registered_address_point"] = point

            _, was_created = Developer.objects.update_or_create(
                company_code=row["company_code"],
                defaults=defaults,
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(f"Done: {created} created, {updated} updated, {skipped} skipped")
        )
