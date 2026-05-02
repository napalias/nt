from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.developers.models import Developer

SAMPLE_CSV_HEADER = (
    "ja_kodas,ja_pavadinimas,evrk_kodas,adresas,iregistravimo_data,statusas,darbuotoju_skaicius"
)
SAMPLE_CSV_ROWS = """\
300000001,UAB Statybos meistras,41.10,Vilniaus g. 1 Vilnius,2010-05-15,active,25
300000002,UAB NT Projektai,"68.10;68.20",Kauno g. 5 Kaunas,2015-07-01,active,10
300000003,UAB Maisto gamyba,56.10,Klaipedos g. 3 Klaipeda,2012-03-20,active,100
300000004,UAB Senoji statyba,41.20,Siauliu g. 8 Siauliai,2005-01-10,liquidated,0
"""
SAMPLE_CSV = f"{SAMPLE_CSV_HEADER}\n{SAMPLE_CSV_ROWS}"

SAMPLE_JSON = json.dumps(
    [
        {
            "ja_kodas": "300000010",
            "ja_pavadinimas": "UAB JSON Developer",
            "evrk_kodas": "41.10",
            "adresas": "Vilnius g. 1",
            "iregistravimo_data": "2020-01-15",
            "statusas": "active",
            "darbuotoju_skaicius": "30",
        },
        {
            "ja_kodas": "300000011",
            "ja_pavadinimas": "UAB JSON Irrelevant",
            "evrk_kodas": "10.10",
            "adresas": "Kaunas g. 2",
            "iregistravimo_data": "2019-06-01",
            "statusas": "active",
            "darbuotoju_skaicius": "5",
        },
        {
            "ja_kodas": "300000012",
            "ja_pavadinimas": "UAB JSON Agency",
            "evrk_kodas": "68.31",
            "adresas": "Klaipėda g. 3",
            "iregistravimo_data": "2021-11-20",
            "statusas": "active",
            "darbuotoju_skaicius": "8",
        },
    ]
)


def _write_temp_file(content: str, suffix: str) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return Path(f.name)


@pytest.mark.django_db
class TestImportJarDumpCSV:
    def test_imports_relevant_nace_only(self):
        filepath = _write_temp_file(SAMPLE_CSV, ".csv")
        try:
            call_command("import_jar_dump", str(filepath), "--no-geocode")
            # NACE 56.10 should be excluded, so 3 out of 4 rows imported
            assert Developer.objects.count() == 3
            assert not Developer.objects.filter(company_code="300000003").exists()
        finally:
            filepath.unlink()

    def test_creates_correct_records(self):
        filepath = _write_temp_file(SAMPLE_CSV, ".csv")
        try:
            call_command("import_jar_dump", str(filepath), "--no-geocode")
            dev = Developer.objects.get(company_code="300000001")
            assert dev.name == "UAB Statybos meistras"
            assert dev.nace_codes == ["41.10"]
            assert dev.registered_address == "Vilniaus g. 1 Vilnius"
            assert dev.status == "active"
            assert dev.employee_count == 25
        finally:
            filepath.unlink()

    def test_semicolon_separated_nace(self):
        filepath = _write_temp_file(SAMPLE_CSV, ".csv")
        try:
            call_command("import_jar_dump", str(filepath), "--no-geocode")
            dev = Developer.objects.get(company_code="300000002")
            assert dev.nace_codes == ["68.10", "68.20"]
        finally:
            filepath.unlink()

    def test_upsert_on_rerun(self):
        filepath = _write_temp_file(SAMPLE_CSV, ".csv")
        try:
            call_command("import_jar_dump", str(filepath), "--no-geocode")
            assert Developer.objects.count() == 3
            # Run again — should update, not duplicate
            call_command("import_jar_dump", str(filepath), "--no-geocode")
            assert Developer.objects.count() == 3
        finally:
            filepath.unlink()

    def test_dry_run_no_writes(self):
        filepath = _write_temp_file(SAMPLE_CSV, ".csv")
        try:
            call_command("import_jar_dump", str(filepath), "--no-geocode", "--dry-run")
            assert Developer.objects.count() == 0
        finally:
            filepath.unlink()


@pytest.mark.django_db
class TestImportJarDumpJSON:
    def test_imports_from_json(self):
        filepath = _write_temp_file(SAMPLE_JSON, ".json")
        try:
            call_command("import_jar_dump", str(filepath), "--no-geocode")
            # 10.10 is not relevant, so 2 out of 3 imported
            assert Developer.objects.count() == 2
            assert Developer.objects.filter(company_code="300000010").exists()
            assert Developer.objects.filter(company_code="300000012").exists()
            assert not Developer.objects.filter(company_code="300000011").exists()
        finally:
            filepath.unlink()

    def test_json_record_fields(self):
        filepath = _write_temp_file(SAMPLE_JSON, ".json")
        try:
            call_command("import_jar_dump", str(filepath), "--no-geocode")
            dev = Developer.objects.get(company_code="300000010")
            assert dev.name == "UAB JSON Developer"
            assert dev.employee_count == 30
        finally:
            filepath.unlink()


@pytest.mark.django_db
class TestImportJarDumpGeocoding:
    @patch("apps.developers.management.commands.import_jar_dump._geocode_address")
    @patch("apps.developers.management.commands.import_jar_dump.time.sleep")
    def test_geocode_called_per_row(self, mock_sleep, mock_geocode):
        from django.contrib.gis.geos import Point

        mock_geocode.return_value = Point(25.28, 54.69, srid=4326)

        filepath = _write_temp_file(SAMPLE_CSV, ".csv")
        try:
            call_command("import_jar_dump", str(filepath))
            # 3 relevant rows → 3 geocode calls
            assert mock_geocode.call_count == 3
            # Rate limiting sleep called between geocode requests
            assert mock_sleep.call_count == 3

            dev = Developer.objects.get(company_code="300000001")
            assert dev.registered_address_point is not None
            assert dev.registered_address_point.x == pytest.approx(25.28)
        finally:
            filepath.unlink()

    @patch("apps.developers.management.commands.import_jar_dump._geocode_address")
    @patch("apps.developers.management.commands.import_jar_dump.time.sleep")
    def test_geocode_failure_leaves_null(self, mock_sleep, mock_geocode):
        mock_geocode.return_value = None

        filepath = _write_temp_file(SAMPLE_CSV, ".csv")
        try:
            call_command("import_jar_dump", str(filepath))
            dev = Developer.objects.get(company_code="300000001")
            assert dev.registered_address_point is None
        finally:
            filepath.unlink()


class TestImportJarDumpErrors:
    def test_missing_file(self):
        with pytest.raises(CommandError, match="File not found"):
            call_command("import_jar_dump", "/nonexistent/path.csv", "--no-geocode")

    def test_unsupported_format(self):
        filepath = _write_temp_file("data", ".xml")
        try:
            with pytest.raises(CommandError, match="Unsupported file format"):
                call_command("import_jar_dump", str(filepath), "--no-geocode")
        finally:
            filepath.unlink()
