from django.contrib import admin

from apps.search.models import SavedSearch


@admin.register(SavedSearch)
class SavedSearchAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "lat",
        "lng",
        "radius_m",
        "listing_type",
        "property_type",
        "is_active",
        "last_notified_at",
        "created_at",
    ]
    list_filter = ["is_active", "listing_type", "property_type"]
    search_fields = ["name"]
    readonly_fields = ["created_at", "last_notified_at"]
