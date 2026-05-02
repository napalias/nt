from django.contrib.gis.geos import Point
from model_bakery.recipe import Recipe, seq

from apps.listings.models import Listing

listing_recipe = Recipe(
    Listing,
    source="domoplius",
    source_url=seq("https://domoplius.lt/skelbimai/", suffix=".html"),
    source_id=seq("DOM-"),
    content_hash=seq("hash_"),
    title=seq("Parduodamas namas Kretingoje #"),
    property_type=Listing.PropertyType.HOUSE,
    listing_type=Listing.ListingType.SALE,
    price=200_000,
    area_sqm=100,
    plot_area_ares=10,
    rooms=4,
    year_built=2024,
    building_type=Listing.BuildingType.FRAME,
    is_new_construction=True,
    address_raw="Kretinga, Kretingos r. sav.",
    city="Kretinga",
    municipality="Kretingos r. sav.",
    location=Point(21.2420, 55.8835, srid=4326),
    photo_urls=["https://example.com/photo1.jpg"],
)

flat_recipe = Recipe(
    Listing,
    source="aruodas",
    source_url=seq("https://www.aruodas.lt/butai/", suffix=".html"),
    source_id=seq("ARU-"),
    content_hash=seq("hash_flat_"),
    title=seq("Parduodamas butas Vilniuje #"),
    property_type=Listing.PropertyType.FLAT,
    listing_type=Listing.ListingType.SALE,
    price=150_000,
    area_sqm=65,
    rooms=2,
    floor=3,
    total_floors=5,
    year_built=2020,
    building_type=Listing.BuildingType.BRICK,
    is_new_construction=False,
    address_raw="Vilnius, Žvėrynas",
    city="Vilnius",
    municipality="Vilniaus m. sav.",
    district="Žvėrynas",
    location=Point(25.2500, 54.6950, srid=4326),
    photo_urls=["https://example.com/flat1.jpg"],
)
