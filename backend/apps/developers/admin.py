from django.contrib.gis import admin as gis_admin

from apps.developers.models import Developer


@gis_admin.register(Developer)
class DeveloperAdmin(gis_admin.GISModelAdmin):
    list_display = [
        "name",
        "company_code",
        "status",
        "founded",
        "employee_count",
        "last_synced_at",
    ]
    list_filter = ["status"]
    search_fields = ["name", "company_code"]
    readonly_fields = ["last_synced_at"]
    list_per_page = 50

    fieldsets = [
        (
            "Pagrindinė informacija",
            {"fields": ("company_code", "name", "nace_codes", "status")},
        ),
        (
            "Adresas",
            {"fields": ("registered_address", "registered_address_point")},
        ),
        (
            "Papildoma",
            {"fields": ("founded", "employee_count", "last_synced_at")},
        ),
    ]
