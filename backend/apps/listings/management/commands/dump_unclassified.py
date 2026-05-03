import json

from django.core.management.base import BaseCommand

from apps.listings.models import Listing


class Command(BaseCommand):
    help = "Dump unclassified active listings as JSON for Claude Code evaluation"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument("--city", type=str, default="")

    def handle(self, *args, **options):
        qs = Listing.objects.filter(is_active=True, evaluation__isnull=True)
        if options["city"]:
            qs = qs.filter(city__icontains=options["city"])
        qs = qs.order_by("-scraped_at")[: options["limit"]]

        listings = []
        for listing in qs:
            listings.append(
                {
                    "id": listing.pk,
                    "title": listing.title,
                    "source": listing.source,
                    "source_url": listing.source_url,
                    "price": float(listing.price) if listing.price else None,
                    "area_sqm": float(listing.area_sqm) if listing.area_sqm else None,
                    "plot_area_ares": float(listing.plot_area_ares)
                    if listing.plot_area_ares
                    else None,
                    "rooms": listing.rooms,
                    "year_built": listing.year_built,
                    "building_type": listing.get_building_type_display()
                    if listing.building_type
                    else "",
                    "heating_type": listing.heating_type,
                    "energy_class": listing.energy_class,
                    "is_new_construction": listing.is_new_construction,
                    "address": listing.address_raw,
                    "city": listing.city,
                    "district": listing.district,
                    "description": (listing.description or "")[:500],
                    "photo_count": len(listing.photo_urls) if listing.photo_urls else 0,
                }
            )

        self.stdout.write(json.dumps(listings, ensure_ascii=False, indent=2))
