from django.contrib.gis import admin as gis_admin

from apps.permits.models import BuildingPermit


@gis_admin.register(BuildingPermit)
class BuildingPermitAdmin(gis_admin.GISModelAdmin):
    list_display = [
        "permit_number",
        "permit_type",
        "status",
        "issued_at",
        "applicant_name",
        "address_raw",
        "project_type",
        "building_purpose",
    ]
    list_filter = ["status", "permit_type", "project_type", "building_purpose"]
    search_fields = [
        "permit_number",
        "applicant_name",
        "address_raw",
        "cadastral_number",
        "project_description",
    ]
    readonly_fields = ["created_at", "updated_at", "scraped_at"]
    list_per_page = 50
    raw_id_fields = ["applicant", "contractor", "plot"]

    fieldsets = [
        (
            "Leidimas",
            {"fields": ("permit_number", "permit_type", "status", "issued_at")},
        ),
        (
            "Dalyviai",
            {"fields": ("applicant_name", "applicant", "contractor")},
        ),
        (
            "Vieta",
            {"fields": ("address_raw", "cadastral_number", "location", "plot")},
        ),
        (
            "Projektas",
            {
                "fields": (
                    "project_description",
                    "project_type",
                    "building_purpose",
                )
            },
        ),
        (
            "Šaltinis",
            {"fields": ("source_url", "raw_data"), "classes": ("collapse",)},
        ),
        (
            "Laikas",
            {"fields": ("scraped_at", "created_at", "updated_at")},
        ),
    ]
