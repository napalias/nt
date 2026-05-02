from django.contrib.gis.db import models as gis_models
from django.db import models
from django.utils import timezone


class CadastralPlot(models.Model):
    """Kadastro sklypas iš GeoPortal NTKR sluoksnio."""

    cadastral_number = models.CharField(max_length=64, unique=True, verbose_name="Kadastro numeris")
    geometry = gis_models.MultiPolygonField(geography=True, srid=4326, verbose_name="Geometrija")
    area_sqm = models.FloatField(verbose_name="Plotas kv.m")
    purpose = models.CharField(max_length=200, blank=True, verbose_name="Paskirtis")
    purpose_category = models.CharField(
        max_length=64, blank=True, verbose_name="Paskirties kategorija"
    )
    municipality = models.CharField(max_length=100, blank=True, verbose_name="Savivaldybė")
    synced_at = models.DateTimeField(default=timezone.now, verbose_name="Sinchronizuota")

    class Meta:
        verbose_name = "Kadastro sklypas"
        verbose_name_plural = "Kadastro sklypai"
        ordering = ["-synced_at"]

    def __str__(self) -> str:
        return f"{self.cadastral_number} ({self.municipality})"


class HeritageObject(models.Model):
    """Kultūros paveldo objektas iš KVR / GeoPortal."""

    kvr_code = models.CharField(max_length=64, unique=True, verbose_name="KVR kodas")
    name = models.CharField(max_length=500, verbose_name="Pavadinimas")
    category = models.CharField(max_length=100, blank=True, verbose_name="Kategorija")
    protection_level = models.CharField(max_length=64, blank=True, verbose_name="Apsaugos lygis")
    geometry = gis_models.GeometryField(geography=True, srid=4326, verbose_name="Geometrija")
    synced_at = models.DateTimeField(default=timezone.now, verbose_name="Sinchronizuota")

    class Meta:
        verbose_name = "Kultūros paveldo objektas"
        verbose_name_plural = "Kultūros paveldo objektai"
        ordering = ["-synced_at"]

    def __str__(self) -> str:
        return f"{self.kvr_code} — {self.name}"


class SpecialLandUseCondition(models.Model):
    """SŽNS: specialiosios žemės naudojimo sąlygos."""

    category = models.CharField(max_length=200, verbose_name="Kategorija")
    geometry = gis_models.MultiPolygonField(geography=True, srid=4326, verbose_name="Geometrija")
    description = models.TextField(blank=True, verbose_name="Aprašymas")
    synced_at = models.DateTimeField(default=timezone.now, verbose_name="Sinchronizuota")

    class Meta:
        verbose_name = "Specialioji žemės naudojimo sąlyga"
        verbose_name_plural = "Specialiosios žemės naudojimo sąlygos"
        ordering = ["-synced_at"]

    def __str__(self) -> str:
        return self.category
