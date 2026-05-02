from django.contrib.gis import admin as gis_admin

from apps.cadastre.models import CadastralPlot, HeritageObject, SpecialLandUseCondition


@gis_admin.register(CadastralPlot)
class CadastralPlotAdmin(gis_admin.GISModelAdmin):
    list_display = [
        "cadastral_number",
        "area_sqm",
        "purpose",
        "purpose_category",
        "municipality",
        "synced_at",
    ]
    list_filter = ["purpose_category", "municipality"]
    search_fields = ["cadastral_number", "purpose", "municipality"]
    readonly_fields = ["synced_at"]
    list_per_page = 50


@gis_admin.register(HeritageObject)
class HeritageObjectAdmin(gis_admin.GISModelAdmin):
    list_display = [
        "kvr_code",
        "name",
        "category",
        "protection_level",
        "synced_at",
    ]
    list_filter = ["category", "protection_level"]
    search_fields = ["kvr_code", "name", "category"]
    readonly_fields = ["synced_at"]
    list_per_page = 50


@gis_admin.register(SpecialLandUseCondition)
class SpecialLandUseConditionAdmin(gis_admin.GISModelAdmin):
    list_display = [
        "category",
        "description",
        "synced_at",
    ]
    list_filter = ["category"]
    search_fields = ["category", "description"]
    readonly_fields = ["synced_at"]
    list_per_page = 50
