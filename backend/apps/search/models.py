from django.db import models


class SavedSearch(models.Model):
    """A saved search with filters — used for new-listing notifications."""

    name = models.CharField(max_length=200, verbose_name="Pavadinimas")
    lat = models.FloatField(verbose_name="Platuma")
    lng = models.FloatField(verbose_name="Ilguma")
    radius_m = models.IntegerField(default=5000, verbose_name="Spindulys (m)")
    min_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Min kaina"
    )
    max_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Max kaina"
    )
    rooms = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Kambariai")
    property_type = models.CharField(max_length=16, blank=True, verbose_name="Turto tipas")
    listing_type = models.CharField(max_length=8, default="sale", verbose_name="Skelbimo tipas")
    is_new_construction = models.BooleanField(null=True, verbose_name="Naujos statybos")
    last_notified_at = models.DateTimeField(auto_now_add=True, verbose_name="Pask. pranešimas")
    is_active = models.BooleanField(default=True, verbose_name="Aktyvus")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Sukurta")

    class Meta:
        verbose_name = "Išsaugotas paieška"
        verbose_name_plural = "Išsaugotos paieškos"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name
