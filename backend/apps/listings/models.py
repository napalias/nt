from django.contrib.gis.db import models as gis_models
from django.db import models


class Listing(models.Model):
    class PropertyType(models.TextChoices):
        HOUSE = "house", "Namas"
        FLAT = "flat", "Butas"
        PLOT = "plot", "Sklypas"
        COMMERCIAL = "commercial", "Komercinis"
        COTTAGE = "cottage", "Sodyba"
        PART_OF_HOUSE = "part_of_house", "Namo dalis"

    class ListingType(models.TextChoices):
        SALE = "sale", "Pardavimas"
        RENT = "rent", "Nuoma"

    class BuildingType(models.TextChoices):
        BRICK = "brick", "Mūrinis"
        BLOCK = "block", "Blokinis"
        WOODEN = "wooden", "Medinis"
        MONOLITHIC = "monolithic", "Monolitinis"
        LOG = "log", "Rąstinis"
        FRAME = "frame", "Karkasinis"
        OTHER = "other", "Kita"

    # --- Source tracking ---
    source = models.CharField(max_length=32, db_index=True, verbose_name="Šaltinis")
    source_url = models.URLField(max_length=500, verbose_name="Šaltinio nuoroda")
    source_id = models.CharField(max_length=64, verbose_name="Šaltinio ID")
    content_hash = models.CharField(max_length=64, db_index=True, verbose_name="Turinio hash")

    # --- Core listing fields ---
    title = models.CharField(max_length=500, verbose_name="Pavadinimas")
    description = models.TextField(blank=True, verbose_name="Aprašymas")
    property_type = models.CharField(
        max_length=16, choices=PropertyType.choices, db_index=True, verbose_name="Turto tipas"
    )
    listing_type = models.CharField(
        max_length=8, choices=ListingType.choices, db_index=True, verbose_name="Skelbimo tipas"
    )

    # --- Price ---
    price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Kaina"
    )
    price_per_sqm = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Kaina už kv.m"
    )
    currency = models.CharField(max_length=3, default="EUR", verbose_name="Valiuta")

    # --- Size ---
    area_sqm = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Plotas kv.m"
    )
    plot_area_ares = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Sklypo plotas arais"
    )
    rooms = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Kambariai")

    # --- Building details ---
    floor = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Aukštas")
    total_floors = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="Aukštų skaičius"
    )
    year_built = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="Statybos metai"
    )
    building_type = models.CharField(
        max_length=16,
        choices=BuildingType.choices,
        blank=True,
        verbose_name="Pastato tipas",
    )
    heating_type = models.CharField(max_length=64, blank=True, verbose_name="Šildymas")
    energy_class = models.CharField(max_length=8, blank=True, verbose_name="Energijos klasė")
    is_new_construction = models.BooleanField(
        default=False, db_index=True, verbose_name="Naujos statybos"
    )

    # --- Location ---
    address_raw = models.CharField(max_length=500, verbose_name="Adresas (originalus)")
    city = models.CharField(max_length=100, blank=True, db_index=True, verbose_name="Miestas")
    municipality = models.CharField(
        max_length=100, blank=True, db_index=True, verbose_name="Savivaldybė"
    )
    district = models.CharField(max_length=100, blank=True, verbose_name="Rajonas / mikrorajonas")
    location = gis_models.PointField(geography=True, srid=4326, verbose_name="Koordinatės")
    cadastral_number = models.CharField(
        max_length=64, blank=True, db_index=True, verbose_name="Kadastro numeris"
    )

    # --- Media ---
    photo_urls = models.JSONField(default=list, verbose_name="Nuotraukų nuorodos")

    # --- Timestamps ---
    first_seen_at = models.DateTimeField(auto_now_add=True, verbose_name="Pirmas pastebėjimas")
    last_seen_at = models.DateTimeField(auto_now=True, verbose_name="Paskutinis pastebėjimas")
    scraped_at = models.DateTimeField(verbose_name="Nuskaityta")
    is_active = models.BooleanField(default=True, db_index=True, verbose_name="Aktyvus")

    # --- Raw data ---
    raw_data = models.JSONField(default=dict, blank=True, verbose_name="Neapdoroti duomenys")

    class Meta:
        verbose_name = "Skelbimas"
        verbose_name_plural = "Skelbimai"
        ordering = ["-scraped_at"]
        constraints = [
            models.UniqueConstraint(fields=["source", "source_id"], name="unique_source_listing"),
        ]
        indexes = [
            models.Index(fields=["property_type", "listing_type", "is_active"]),
            models.Index(fields=["price"]),
            models.Index(fields=["area_sqm"]),
            models.Index(fields=["rooms"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.source})"

    def save(self, *args, **kwargs) -> None:
        if self.area_sqm and self.price and self.area_sqm > 0:
            self.price_per_sqm = self.price / self.area_sqm
        else:
            self.price_per_sqm = None
        super().save(*args, **kwargs)
