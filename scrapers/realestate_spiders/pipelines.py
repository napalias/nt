from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime

import django_setup

django_setup.setup()

import requests  # noqa: E402
from django.conf import settings  # noqa: E402
from django.contrib.gis.geos import Point  # noqa: E402
from scrapy import Spider  # noqa: E402
from scrapy.exceptions import DropItem  # noqa: E402

from realestate_spiders.items import ListingItem, PermitItem, PlanningItem  # noqa: E402

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ["url", "source", "title", "address", "property_type", "listing_type"]


class ValidatePipeline:
    """Drop items missing required fields. Only processes ListingItem."""

    def process_item(self, item: ListingItem, spider: Spider) -> ListingItem:
        if not isinstance(item, ListingItem):
            return item
        for field in REQUIRED_FIELDS:
            if not item.get(field):
                raise DropItem(f"Missing required field: {field}")
        return item


class NormalizePipeline:
    """Clean and normalize field values, compute content_hash. Only processes ListingItem."""

    def process_item(self, item: ListingItem, spider: Spider) -> ListingItem:
        if not isinstance(item, ListingItem):
            return item
        if item.get("title"):
            item["title"] = item["title"].strip()

        if item.get("description"):
            item["description"] = item["description"].strip()

        if item.get("price"):
            item["price"] = self._parse_number(item["price"])
        if item.get("area_sqm"):
            item["area_sqm"] = self._parse_number(item["area_sqm"])
        if item.get("plot_area_ares"):
            item["plot_area_ares"] = self._parse_number(item["plot_area_ares"])
        if item.get("rooms"):
            item["rooms"] = self._parse_int(item["rooms"])
        if item.get("floor"):
            item["floor"] = self._parse_int(item["floor"])
        if item.get("total_floors"):
            item["total_floors"] = self._parse_int(item["total_floors"])
        if item.get("year_built"):
            item["year_built"] = self._parse_int(item["year_built"])

        if item.get("address"):
            item["address"] = item["address"].strip()
        if item.get("city"):
            item["city"] = item["city"].strip()

        if not item.get("currency"):
            item["currency"] = "EUR"

        if not item.get("scraped_at"):
            item["scraped_at"] = datetime.now(UTC).isoformat()

        if not item.get("source_id"):
            item["source_id"] = self._extract_source_id(item["url"])

        item["_content_hash"] = self._compute_hash(item)

        return item

    def _parse_number(self, value) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            cleaned = re.sub(r"[^\d.,]", "", value)
            cleaned = cleaned.replace(",", ".")
            try:
                return float(cleaned)
            except ValueError:
                return None
        return None

    def _parse_int(self, value) -> int | None:
        if isinstance(value, int):
            return value
        if isinstance(value, (float, str)):
            try:
                return int(float(str(value).replace(",", ".")))
            except (ValueError, TypeError):
                return None
        return None

    def _extract_source_id(self, url: str) -> str:
        match = re.search(r"-(\d+)\.html", url)
        if match:
            return match.group(1)
        return hashlib.md5(url.encode()).hexdigest()[:16]

    def _compute_hash(self, item: ListingItem) -> str:
        parts = [
            str(item.get("source", "")),
            str(item.get("url", "")),
            str(item.get("price", "")),
            str(item.get("area_sqm", "")),
            str(item.get("rooms", "")),
            str(item.get("title", "")),
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()


class GeocodePipeline:
    """Geocode address via Nominatim if lat/lng are missing.

    If Nominatim is unavailable, uses city-center fallback coordinates
    so items aren't dropped. Imprecise but better than losing the listing.
    """

    CITY_FALLBACK = {
        "kretinga": (55.8835, 21.2420),
        "palanga": (55.9175, 21.0686),
        "klaipėda": (55.7033, 21.1443),
        "klaipeda": (55.7033, 21.1443),
        "vilnius": (54.6872, 25.2797),
        "kaunas": (54.8985, 23.9036),
        "šiauliai": (55.9349, 23.3137),
        "siauliai": (55.9349, 23.3137),
        "panevėžys": (55.7348, 24.3575),
        "panevezys": (55.7348, 24.3575),
        "alytus": (54.3963, 24.0459),
        "marijampolė": (54.5593, 23.3500),
        "marijampole": (54.5593, 23.3500),
        "mažeikiai": (56.3092, 22.3423),
        "mazeikiai": (56.3092, 22.3423),
        "jonava": (55.0724, 24.2800),
        "utena": (55.4980, 25.6027),
        "kėdainiai": (55.2878, 23.9833),
        "kedainiai": (55.2878, 23.9833),
        "telšiai": (55.9864, 22.2472),
        "telsiai": (55.9864, 22.2472),
        "tauragė": (55.2513, 22.2900),
        "taurage": (55.2513, 22.2900),
        "ukmergė": (55.2453, 24.7706),
        "ukmerge": (55.2453, 24.7706),
        "visaginas": (55.5980, 26.4310),
        "plungė": (55.9117, 21.8453),
        "plunge": (55.9117, 21.8453),
        "raseiniai": (55.3822, 23.1178),
        "radviliškis": (55.8100, 23.5400),
        "radviliskis": (55.8100, 23.5400),
        "biržai": (56.2019, 24.7544),
        "birzai": (56.2019, 24.7544),
        "gargždai": (55.7125, 21.3917),
        "gargzdai": (55.7125, 21.3917),
        "kartena": (55.9275, 21.4900),
        "skuodas": (56.2686, 21.5300),
    }

    JUNK_WORDS = {
        "namai", "kotedžai", "butai", "sklypai", "sodai",
        "pardavimui", "nuomai", "pardavimas", "nuoma",
        "komercinis", "garažai", "patalpos",
    }

    def __init__(self):
        self.nominatim_url = getattr(settings, "NOMINATIM_URL", "http://nominatim:8080")

    def _clean_address(self, address: str) -> str:
        parts = [p.strip() for p in address.split(",")]
        clean = [p for p in parts if p.lower() not in self.JUNK_WORDS]
        return ", ".join(clean)

    def process_item(self, item: ListingItem, spider: Spider) -> ListingItem:
        if not isinstance(item, ListingItem):
            return item
        if item.get("latitude") and item.get("longitude"):
            return item

        address = self._clean_address(item.get("address", ""))
        city = item.get("city", "")
        query = f"{address}, {city}" if city and city.lower() not in address.lower() else address

        if not query.strip():
            self._apply_fallback(item)
            return item

        try:
            resp = requests.get(
                f"{self.nominatim_url}/search",
                params={
                    "q": query,
                    "format": "json",
                    "limit": 1,
                    "countrycodes": "lt",
                },
                timeout=5,
            )
            resp.raise_for_status()
            results = resp.json()

            if results:
                item["latitude"] = float(results[0]["lat"])
                item["longitude"] = float(results[0]["lon"])
                logger.debug("Geocoded '%s' → (%s, %s)", query, item["latitude"], item["longitude"])
                return item

        except requests.RequestException:
            logger.warning("Nominatim unavailable for: %s", query)

        self._apply_fallback(item)
        return item

    def _apply_fallback(self, item: ListingItem) -> None:
        city = (item.get("city") or "").lower()
        address = (item.get("address") or "").lower()
        for name, (lat, lng) in self.CITY_FALLBACK.items():
            if name in city or name in address:
                item["latitude"] = lat
                item["longitude"] = lng
                logger.info("Fallback geocode: %s → %s center", item.get("city"), name)
                return
        raise DropItem(
            f"Cannot geocode and no fallback for: {item.get('address')}"
        )


class DjangoWritePipeline:
    """Upsert scraped listings into the Django Listing model. Only processes ListingItem."""

    def _num(self, val, type_fn=float):
        if val is None or val == "":
            return None
        try:
            return type_fn(val)
        except (ValueError, TypeError):
            return None

    def _int(self, val):
        return self._num(val, int)

    def process_item(self, item: ListingItem, spider: Spider) -> ListingItem:
        if not isinstance(item, ListingItem):
            return item
        from apps.listings.models import Listing

        def s(val, maxlen=None):
            v = str(val or "").strip()
            return v[:maxlen] if maxlen else v

        defaults = {
            "source_url": item["url"][:500],
            "content_hash": s(item.get("_content_hash"), 64),
            "title": s(item.get("title"), 500),
            "description": item.get("description", ""),
            "property_type": s(item.get("property_type", "house"), 16),
            "listing_type": s(item.get("listing_type", "sale"), 8),
            "price": self._num(item.get("price")),
            "currency": s(item.get("currency", "EUR"), 3),
            "area_sqm": self._num(item.get("area_sqm")),
            "plot_area_ares": self._num(item.get("plot_area_ares")),
            "rooms": self._int(item.get("rooms")),
            "floor": self._int(item.get("floor")),
            "total_floors": self._int(item.get("total_floors")),
            "year_built": self._int(item.get("year_built")),
            "building_type": s(item.get("building_type"), 16),
            "heating_type": s(item.get("heating_type"), 64),
            "energy_class": s(item.get("energy_class"), 8),
            "is_new_construction": item.get("is_new_construction", False),
            "address_raw": item.get("address", ""),
            "city": item.get("city", ""),
            "municipality": item.get("municipality", ""),
            "district": item.get("district", ""),
            "location": Point(
                float(item["longitude"]),
                float(item["latitude"]),
                srid=4326,
            ),
            "cadastral_number": item.get("cadastral_number", ""),
            "photo_urls": item.get("photo_urls", []),
            "scraped_at": datetime.fromisoformat(item["scraped_at"]),
            "is_active": True,
            "raw_data": dict(item.get("raw_data", {})),
        }

        obj, created = Listing.objects.update_or_create(
            source=s(item["source"], 32),
            source_id=s(item["source_id"], 64),
            defaults=defaults,
        )

        action = "Created" if created else "Updated"
        logger.info("%s listing %s: %s", action, obj.pk, obj.title[:60])

        return item


class PlanningWritePipeline:
    """Upsert planning documents from TPDRIS into Django models."""

    def process_item(self, item: PlanningItem, spider: Spider) -> PlanningItem:
        if not isinstance(item, PlanningItem):
            return item

        from apps.documents.models import Document
        from apps.planning.models import PlanningDocument

        if not item.get("tpdris_id"):
            raise DropItem("Missing tpdris_id")
        if not item.get("title"):
            raise DropItem("Missing title")

        approved_at = None
        if item.get("approved_at"):
            try:
                approved_at = datetime.fromisoformat(item["approved_at"]).date()
            except (ValueError, TypeError):
                approved_at = None

        expires_at = None
        if item.get("expires_at"):
            try:
                expires_at = datetime.fromisoformat(item["expires_at"]).date()
            except (ValueError, TypeError):
                expires_at = None

        scraped_at = datetime.now(UTC)
        if item.get("scraped_at"):
            try:
                scraped_at = datetime.fromisoformat(item["scraped_at"])
            except (ValueError, TypeError):
                pass

        defaults = {
            "title": item["title"],
            "doc_type": item.get("doc_type", "special"),
            "status": item.get("status", "approved"),
            "municipality": item.get("municipality", ""),
            "organizer": item.get("organizer", ""),
            "approved_at": approved_at,
            "expires_at": expires_at,
            "source_url": item.get("source_url", ""),
            "scraped_at": scraped_at,
        }

        obj, created = PlanningDocument.objects.update_or_create(
            tpdris_id=item["tpdris_id"],
            defaults=defaults,
        )

        # Create Document records for linked PDFs
        pdf_links = item.get("pdf_links", [])
        for pdf in pdf_links:
            pdf_url = pdf.get("url", "")
            pdf_title = pdf.get("title", "Document")
            if not pdf_url:
                continue

            doc, _doc_created = Document.objects.get_or_create(
                url=pdf_url,
                defaults={
                    "title": pdf_title,
                    "storage_path": "",
                    "content_hash": hashlib.sha256(pdf_url.encode()).hexdigest(),
                },
            )
            obj.documents.add(doc)

        action = "Created" if created else "Updated"
        logger.info(
            "%s planning document %s: %s (docs: %d)",
            action,
            obj.pk,
            obj.title[:60],
            obj.documents.count(),
        )

        return item


# ---------------------------------------------------------------------------
# Permit pipelines (Phase 8)
# ---------------------------------------------------------------------------

PERMIT_REQUIRED_FIELDS = ["permit_number", "source_url"]


class PermitValidatePipeline:
    """Drop PermitItem instances missing required fields."""

    def process_item(self, item, spider: Spider):
        if not isinstance(item, PermitItem):
            return item
        for field in PERMIT_REQUIRED_FIELDS:
            if not item.get(field):
                raise DropItem(f"Permit missing required field: {field}")
        return item


class PermitMatchPlotPipeline:
    """Match a permit's cadastral_number to a CadastralPlot for geometry."""

    def process_item(self, item, spider: Spider):
        if not isinstance(item, PermitItem):
            return item

        cadastral_number = item.get("cadastral_number", "").strip()
        if not cadastral_number:
            return item

        from apps.cadastre.models import CadastralPlot

        try:
            plot = CadastralPlot.objects.get(cadastral_number=cadastral_number)
            item["_plot_id"] = plot.id
            # Use the centroid of the plot as a fallback location
            item["_plot_centroid"] = plot.geometry.centroid
        except CadastralPlot.DoesNotExist:
            logger.debug("No CadastralPlot found for %s", cadastral_number)
            item["_plot_id"] = None

        return item


class PermitMatchDeveloperPipeline:
    """Fuzzy-match applicant name to Developer records.

    Tries exact match on company code first (if the applicant_name contains
    something that looks like a company code). Falls back to name similarity.
    """

    def process_item(self, item, spider: Spider):
        if not isinstance(item, PermitItem):
            return item

        applicant_name = item.get("applicant_name", "").strip()
        if not applicant_name:
            return item

        from apps.developers.models import Developer

        # 1. Try to extract a company code (Lithuanian: 7-9 digit number)
        code_match = re.search(r"\b(\d{7,9})\b", applicant_name)
        if code_match:
            code = code_match.group(1)
            try:
                dev = Developer.objects.get(company_code=code)
                item["_applicant_id"] = dev.id
                return item
            except Developer.DoesNotExist:
                pass

        # 2. Fuzzy name match using database LIKE
        # Normalize: strip UAB/AB/IĮ prefixes for matching
        name_clean = re.sub(
            r"^(UAB|AB|IĮ|VĮ|MB)\s+",
            "",
            applicant_name.upper(),
        ).strip()
        if len(name_clean) < 3:
            return item

        matches = Developer.objects.filter(name__icontains=name_clean)[:1]
        if matches:
            item["_applicant_id"] = matches[0].id
        else:
            item["_applicant_id"] = None

        return item


class PermitWritePipeline:
    """Upsert scraped permits into the Django BuildingPermit model."""

    def process_item(self, item, spider: Spider):
        if not isinstance(item, PermitItem):
            return item

        from apps.permits.models import BuildingPermit

        # Parse issued_at date
        issued_at = None
        issued_at_raw = item.get("issued_at", "")
        if issued_at_raw:
            for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y.%m.%d"):
                try:
                    issued_at = datetime.strptime(issued_at_raw, fmt).date()
                    break
                except ValueError:
                    continue

        # Build location from plot centroid if available
        location = None
        plot_centroid = item.get("_plot_centroid")
        if plot_centroid:
            location = Point(plot_centroid.x, plot_centroid.y, srid=4326)

        defaults = {
            "permit_type": item.get("permit_type", ""),
            "status": item.get("status", ""),
            "issued_at": issued_at,
            "applicant_name": item.get("applicant_name", ""),
            "applicant_id": item.get("_applicant_id"),
            "cadastral_number": item.get("cadastral_number", ""),
            "address_raw": item.get("address_raw", ""),
            "location": location,
            "plot_id": item.get("_plot_id"),
            "project_description": item.get("project_description", ""),
            "project_type": item.get("project_type", ""),
            "building_purpose": item.get("building_purpose", ""),
            "source_url": item.get("source_url", ""),
            "raw_data": dict(item.get("raw_data", {})),
            "scraped_at": datetime.fromisoformat(item["scraped_at"]),
        }

        obj, created = BuildingPermit.objects.update_or_create(
            permit_number=item["permit_number"],
            defaults=defaults,
        )

        action = "Created" if created else "Updated"
        logger.info("%s permit %s: %s", action, obj.pk, obj.permit_number)

        return item
