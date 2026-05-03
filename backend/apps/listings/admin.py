from django.contrib.gis import admin as gis_admin

from apps.listings.models import ExcludedListing, Listing


@gis_admin.register(Listing)
class ListingAdmin(gis_admin.GISModelAdmin):
    list_display = [
        "title",
        "source",
        "property_type",
        "listing_type",
        "price",
        "area_sqm",
        "rooms",
        "city",
        "is_new_construction",
        "is_active",
        "scraped_at",
    ]
    list_filter = [
        "source",
        "property_type",
        "listing_type",
        "is_new_construction",
        "is_active",
        "city",
    ]
    search_fields = ["title", "address_raw", "city", "cadastral_number", "source_id"]
    readonly_fields = ["first_seen_at", "last_seen_at", "content_hash", "price_per_sqm"]
    list_per_page = 50

    fieldsets = [
        (
            "Šaltinis",
            {"fields": ("source", "source_url", "source_id", "content_hash")},
        ),
        (
            "Pagrindinis",
            {
                "fields": (
                    "title",
                    "description",
                    "property_type",
                    "listing_type",
                    "is_active",
                )
            },
        ),
        (
            "Kaina",
            {"fields": ("price", "price_per_sqm", "currency")},
        ),
        (
            "Dydis",
            {"fields": ("area_sqm", "plot_area_ares", "rooms")},
        ),
        (
            "Pastatas",
            {
                "fields": (
                    "floor",
                    "total_floors",
                    "year_built",
                    "building_type",
                    "heating_type",
                    "energy_class",
                    "is_new_construction",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Vieta",
            {
                "fields": (
                    "address_raw",
                    "city",
                    "municipality",
                    "district",
                    "location",
                    "cadastral_number",
                )
            },
        ),
        (
            "Nuotraukos ir duomenys",
            {"fields": ("photo_urls", "raw_data"), "classes": ("collapse",)},
        ),
        (
            "Laikas",
            {"fields": ("scraped_at", "first_seen_at", "last_seen_at")},
        ),
    ]


@gis_admin.register(ExcludedListing)
class ExcludedListingAdmin(gis_admin.ModelAdmin):
    list_display = ["listing", "reason", "excluded_at"]
    list_filter = ["excluded_at"]
    search_fields = ["listing__title", "reason"]
    readonly_fields = ["excluded_at"]
    raw_id_fields = ["listing"]
