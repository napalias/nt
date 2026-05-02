from datetime import date

import pytest
from django.contrib.gis.geos import MultiPolygon, Point, Polygon
from django.db import IntegrityError
from model_bakery import baker

from apps.cadastre.models import CadastralPlot
from apps.developers.models import Developer
from apps.permits.models import BuildingPermit


def _make_multipolygon() -> MultiPolygon:
    poly = Polygon.from_bbox((25.25, 54.68, 25.30, 54.70))
    return MultiPolygon(poly, srid=4326)


@pytest.mark.django_db
class TestBuildingPermitModel:
    def test_create_permit(self):
        permit = baker.make(
            BuildingPermit,
            permit_number="LSNS-01-23-0001",
            permit_type="Statybos leidimas",
            status="issued",
            issued_at=date(2024, 3, 15),
            applicant_name="UAB Statybos meistras",
            address_raw="Vilniaus g. 1, Vilnius",
            location=Point(25.28, 54.69, srid=4326),
            project_type="new",
            building_purpose="residential",
        )
        assert permit.pk is not None
        assert str(permit) == "LSNS-01-23-0001 (issued)"

    def test_permit_number_unique(self):
        baker.make(
            BuildingPermit,
            permit_number="LSNS-01-23-0002",
            location=Point(25.28, 54.69, srid=4326),
        )
        with pytest.raises(IntegrityError):
            baker.make(
                BuildingPermit,
                permit_number="LSNS-01-23-0002",
                location=Point(25.28, 54.69, srid=4326),
            )

    def test_permit_with_developer_fk(self):
        dev = baker.make(
            Developer,
            company_code="300123456",
            name="UAB Testas",
            status="active",
        )
        permit = baker.make(
            BuildingPermit,
            permit_number="LSNS-01-23-0003",
            applicant=dev,
            applicant_name="UAB Testas",
            location=Point(25.28, 54.69, srid=4326),
        )
        assert permit.applicant == dev
        assert permit.applicant.company_code == "300123456"

    def test_permit_with_plot_fk(self):
        plot = baker.make(
            CadastralPlot,
            cadastral_number="0101/0001:5678",
            geometry=_make_multipolygon(),
            area_sqm=1200.0,
        )
        permit = baker.make(
            BuildingPermit,
            permit_number="LSNS-01-23-0004",
            plot=plot,
            cadastral_number="0101/0001:5678",
            location=Point(25.28, 54.69, srid=4326),
        )
        assert permit.plot == plot
        assert permit.plot.cadastral_number == "0101/0001:5678"

    def test_permit_nullable_fields(self):
        """All optional FKs and fields should accept null/blank."""
        permit = baker.make(
            BuildingPermit,
            permit_number="LSNS-01-23-0005",
            applicant=None,
            contractor=None,
            plot=None,
            location=None,
            issued_at=None,
        )
        assert permit.pk is not None
        assert permit.applicant is None
        assert permit.contractor is None
        assert permit.plot is None
        assert permit.location is None

    def test_ordering_by_issued_at_desc(self):
        baker.make(
            BuildingPermit,
            permit_number="OLD-001",
            issued_at=date(2023, 1, 1),
            location=Point(25.28, 54.69, srid=4326),
        )
        baker.make(
            BuildingPermit,
            permit_number="NEW-001",
            issued_at=date(2024, 6, 1),
            location=Point(25.28, 54.69, srid=4326),
        )
        permits = list(BuildingPermit.objects.all())
        assert permits[0].permit_number == "NEW-001"
        assert permits[1].permit_number == "OLD-001"
