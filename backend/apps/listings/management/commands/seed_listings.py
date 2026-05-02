import hashlib
import random
from decimal import Decimal

from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.listings.models import Listing

KRETINGA_LOCATIONS = [
    {"name": "Kretinga centras", "lat": 55.8835, "lng": 21.2420},
    {"name": "Kretinga šiaurė", "lat": 55.8900, "lng": 21.2550},
    {"name": "Kretinga pietūs", "lat": 55.8750, "lng": 21.2300},
    {"name": "Dupulčiai", "lat": 55.8820, "lng": 21.2100},
    {"name": "Klonaičiai", "lat": 55.8950, "lng": 21.2700},
    {"name": "Jakubavai", "lat": 55.8700, "lng": 21.1900},
    {"name": "Raguviškiai", "lat": 55.9000, "lng": 21.2200},
    {"name": "Kartena", "lat": 55.8600, "lng": 21.1500},
    {"name": "Kretinga vakarai", "lat": 55.8830, "lng": 21.2200},
    {"name": "Kretinga rytai", "lat": 55.8840, "lng": 21.2650},
]

SOURCES = ["domoplius", "aruodas", "skelbiu"]

HOUSE_TITLES = [
    "Parduodamas naujos statybos namas",
    "Šiuolaikiškas namas su sklypu",
    "Erdvus namas Kretingos rajone",
    "Naujos statybos kotedžas",
    "Namas su 10 arų sklypu",
    "Modernaus dizaino namas",
    "Karkasinis namas Kretingoje",
    "Namas netoli centro",
    "Parduodamas gyvenamasis namas",
    "A+ energijos klasės namas",
]


class Command(BaseCommand):
    help = "Seed the database with fake listings around Kretinga"

    def add_arguments(self, parser):
        parser.add_argument("count", type=int, default=20, nargs="?")

    def handle(self, *args, **options):
        count = options["count"]
        now = timezone.now()
        created = 0

        for i in range(count):
            loc = random.choice(KRETINGA_LOCATIONS)
            lat = loc["lat"] + random.uniform(-0.005, 0.005)
            lng = loc["lng"] + random.uniform(-0.005, 0.005)
            source = random.choice(SOURCES)
            source_id = f"SEED-{i:04d}"
            area = Decimal(str(random.randint(95, 120)))
            price = Decimal(str(random.randint(180_000, 250_000)))

            content = f"{source}-{source_id}-{price}-{area}"
            content_hash = hashlib.md5(content.encode()).hexdigest()

            _, was_created = Listing.objects.update_or_create(
                source=source,
                source_id=source_id,
                defaults={
                    "source_url": f"https://{source}.lt/skelbimai/{source_id}.html",
                    "content_hash": content_hash,
                    "title": f"{random.choice(HOUSE_TITLES)} — {loc['name']}",
                    "description": f"Parduodamas {area} kv.m namas {loc['name']}. "
                    f"Sklypas {random.randint(8, 12)} a. "
                    f"Naujos statybos, {random.choice(['karkasinis', 'mūrinis'])}.",
                    "property_type": Listing.PropertyType.HOUSE,
                    "listing_type": Listing.ListingType.SALE,
                    "price": price,
                    "currency": "EUR",
                    "area_sqm": area,
                    "plot_area_ares": Decimal(str(random.randint(8, 12))),
                    "rooms": random.choice([3, 4, 5]),
                    "total_floors": random.choice([1, 2]),
                    "year_built": random.choice([2023, 2024, 2025]),
                    "building_type": random.choice(
                        [
                            Listing.BuildingType.FRAME,
                            Listing.BuildingType.BRICK,
                            Listing.BuildingType.MONOLITHIC,
                        ]
                    ),
                    "heating_type": random.choice(
                        [
                            "Centrinis",
                            "Šilumos siurblys",
                            "Dujinis",
                            "Geoterminis",
                        ]
                    ),
                    "energy_class": random.choice(["A", "A+", "A++", "B"]),
                    "is_new_construction": True,
                    "address_raw": f"{loc['name']}, Kretingos r. sav.",
                    "city": "Kretinga",
                    "municipality": "Kretingos r. sav.",
                    "district": loc["name"],
                    "location": Point(lng, lat, srid=4326),
                    "photo_urls": [
                        f"https://example.com/photos/{source_id}_1.jpg",
                        f"https://example.com/photos/{source_id}_2.jpg",
                    ],
                    "scraped_at": now,
                    "is_active": True,
                },
            )
            if was_created:
                created += 1

        msg = f"Seeded {created} new listings ({count} total processed)"
        self.stdout.write(self.style.SUCCESS(msg))
