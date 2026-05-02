from django.contrib.gis.db import models as gis_models
from django.db import models


class Developer(models.Model):
    """A company in the real estate / construction sector (filtered by NACE from JAR)."""

    company_code = models.CharField(max_length=16, unique=True, verbose_name="Įmonės kodas")
    name = models.CharField(max_length=300, verbose_name="Pavadinimas")
    nace_codes = models.JSONField(default=list, verbose_name="EVRK kodai")
    registered_address = models.CharField(max_length=500, verbose_name="Registruotas adresas")
    registered_address_point = gis_models.PointField(
        geography=True, srid=4326, null=True, blank=True, verbose_name="Adreso koordinatės"
    )
    founded = models.DateField(null=True, blank=True, verbose_name="Įsteigta")
    status = models.CharField(max_length=32, verbose_name="Statusas")
    employee_count = models.IntegerField(null=True, blank=True, verbose_name="Darbuotojų skaičius")
    last_synced_at = models.DateTimeField(auto_now=True, verbose_name="Paskutinis sinchronizavimas")

    class Meta:
        verbose_name = "Vystytojas"
        verbose_name_plural = "Vystytojai"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["name"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.company_code})"
