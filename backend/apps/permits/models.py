from django.contrib.gis.db import models as gis_models
from django.db import models
from django.utils import timezone


class BuildingPermit(models.Model):
    """Statybos leidimas iš Infostatyba / planuojustatyti.lt."""

    permit_number = models.CharField(max_length=64, unique=True, verbose_name="Leidimo numeris")
    permit_type = models.CharField(max_length=64, blank=True, verbose_name="Leidimo tipas")
    status = models.CharField(max_length=32, blank=True, verbose_name="Statusas")
    issued_at = models.DateField(null=True, blank=True, verbose_name="Išdavimo data")

    applicant_name = models.CharField(
        max_length=500, blank=True, verbose_name="Statytojas / užsakovas"
    )
    applicant = models.ForeignKey(
        "developers.Developer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="permits_as_applicant",
        verbose_name="Vystytojas (statytojas)",
    )
    contractor = models.ForeignKey(
        "developers.Developer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="permits_as_contractor",
        verbose_name="Rangovas",
    )

    cadastral_number = models.CharField(
        max_length=64, blank=True, db_index=True, verbose_name="Kadastro numeris"
    )
    address_raw = models.CharField(max_length=500, blank=True, verbose_name="Adresas")
    location = gis_models.PointField(
        geography=True, srid=4326, null=True, blank=True, verbose_name="Koordinatės"
    )
    plot = models.ForeignKey(
        "cadastre.CadastralPlot",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="permits",
        verbose_name="Kadastro sklypas",
    )

    project_description = models.TextField(blank=True, verbose_name="Projekto aprašymas")
    project_type = models.CharField(max_length=64, blank=True, verbose_name="Projekto tipas")
    building_purpose = models.CharField(max_length=64, blank=True, verbose_name="Pastato paskirtis")

    raw_data = models.JSONField(default=dict, verbose_name="Neapdoroti duomenys")

    source_url = models.URLField(max_length=500, blank=True, verbose_name="Šaltinio nuoroda")
    scraped_at = models.DateTimeField(default=timezone.now, verbose_name="Nuskaitymo laikas")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Sukurta")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Atnaujinta")

    class Meta:
        verbose_name = "Statybos leidimas"
        verbose_name_plural = "Statybos leidimai"
        ordering = ["-issued_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["permit_type"]),
            models.Index(fields=["issued_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.permit_number} ({self.status})"
