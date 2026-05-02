from datetime import date

import pytest
from django.contrib.gis.geos import Point
from django.db import IntegrityError
from model_bakery import baker

from apps.developers.models import Developer


@pytest.mark.django_db
class TestDeveloperModel:
    def test_create_developer(self):
        dev = baker.make(
            Developer,
            company_code="300123456",
            name="UAB Statybos meistras",
            nace_codes=["41.10", "68.10"],
            registered_address="Vilniaus g. 1, Vilnius",
            registered_address_point=Point(25.2797, 54.6872, srid=4326),
            founded=date(2010, 5, 15),
            status="active",
            employee_count=25,
        )
        assert dev.pk is not None
        assert str(dev) == "UAB Statybos meistras (300123456)"

    def test_company_code_unique(self):
        baker.make(Developer, company_code="300123456")
        with pytest.raises(IntegrityError):
            baker.make(Developer, company_code="300123456")

    def test_optional_fields_nullable(self):
        dev = baker.make(
            Developer,
            company_code="300111111",
            name="UAB Test",
            registered_address="Kaunas",
            status="active",
            registered_address_point=None,
            founded=None,
            employee_count=None,
        )
        assert dev.pk is not None
        assert dev.registered_address_point is None
        assert dev.founded is None
        assert dev.employee_count is None

    def test_nace_codes_default_empty_list(self):
        dev = Developer(
            company_code="300222222",
            name="UAB Empty NACE",
            registered_address="Test",
            status="active",
        )
        assert dev.nace_codes == []

    def test_ordering_by_name(self):
        baker.make(Developer, company_code="300000002", name="Zebra UAB")
        baker.make(Developer, company_code="300000001", name="Alfa UAB")
        devs = list(Developer.objects.values_list("name", flat=True))
        assert devs == ["Alfa UAB", "Zebra UAB"]

    def test_last_synced_at_auto_set(self):
        dev = baker.make(Developer, company_code="300333333")
        assert dev.last_synced_at is not None
