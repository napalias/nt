from django.contrib.gis import admin as gis_admin

from apps.planning.models import PlanningDocument


@gis_admin.register(PlanningDocument)
class PlanningDocumentAdmin(gis_admin.GISModelAdmin):
    list_display = [
        "tpdris_id",
        "title",
        "doc_type",
        "status",
        "municipality",
        "approved_at",
        "extraction_confidence",
        "scraped_at",
    ]
    list_filter = ["doc_type", "status", "municipality"]
    search_fields = ["tpdris_id", "title", "municipality", "organizer"]
    readonly_fields = ["scraped_at"]
    list_per_page = 50

    fieldsets = [
        (
            "Pagrindinė informacija",
            {
                "fields": (
                    "tpdris_id",
                    "title",
                    "doc_type",
                    "status",
                    "municipality",
                    "organizer",
                    "source_url",
                ),
            },
        ),
        (
            "Datos",
            {"fields": ("approved_at", "expires_at", "scraped_at")},
        ),
        (
            "Geografija",
            {"fields": ("boundary",)},
        ),
        (
            "Ištraukti duomenys (LLM)",
            {
                "fields": (
                    "allowed_uses",
                    "max_height_m",
                    "max_floors",
                    "max_density",
                    "parking_requirements",
                    "extraction_confidence",
                ),
            },
        ),
        (
            "Dokumentai",
            {"fields": ("documents",)},
        ),
    ]
