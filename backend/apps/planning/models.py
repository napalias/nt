from django.contrib.gis.db import models as gis_models
from django.db import models
from django.utils import timezone


class PlanningDocument(models.Model):
    """Teritorijų planavimo dokumentas iš TPDRIS."""

    DOC_TYPES = [
        ("master", "Bendrasis planas"),
        ("detailed", "Detalusis planas"),
        ("special", "Specialusis planas"),
    ]
    STATUSES = [
        ("preparation", "Rengiamas"),
        ("public_review", "Viešas svarstymas"),
        ("approved", "Patvirtintas"),
        ("rejected", "Atmestas"),
        ("expired", "Nebegalioja"),
    ]

    tpdris_id = models.CharField(max_length=64, unique=True, verbose_name="TPDRIS identifikatorius")
    title = models.CharField(max_length=500, verbose_name="Pavadinimas")
    doc_type = models.CharField(max_length=16, choices=DOC_TYPES, verbose_name="Dokumento tipas")
    status = models.CharField(max_length=16, choices=STATUSES, verbose_name="Būsena")
    municipality = models.CharField(max_length=100, verbose_name="Savivaldybė")
    organizer = models.CharField(max_length=300, blank=True, verbose_name="Organizatorius")
    approved_at = models.DateField(null=True, blank=True, verbose_name="Patvirtinimo data")
    expires_at = models.DateField(null=True, blank=True, verbose_name="Galiojimo pabaiga")

    boundary = gis_models.MultiPolygonField(
        geography=True, srid=4326, null=True, blank=True, verbose_name="Ribos"
    )

    # LLM extraction fields
    allowed_uses = models.JSONField(default=list, verbose_name="Leistinos paskirtys")
    max_height_m = models.FloatField(null=True, blank=True, verbose_name="Maks. aukštis (m)")
    max_floors = models.IntegerField(null=True, blank=True, verbose_name="Maks. aukštų skaičius")
    max_density = models.FloatField(null=True, blank=True, verbose_name="Maks. užstatymo tankis")
    parking_requirements = models.TextField(
        blank=True, verbose_name="Automobilių stovėjimo reikalavimai"
    )
    extraction_confidence = models.FloatField(
        null=True, blank=True, verbose_name="Ištraukimo patikimumas"
    )

    documents = models.ManyToManyField("documents.Document", blank=True, verbose_name="Dokumentai")
    source_url = models.URLField(verbose_name="Šaltinio URL")
    scraped_at = models.DateTimeField(default=timezone.now, verbose_name="Nuskanuota")

    class Meta:
        verbose_name = "Teritorijų planavimo dokumentas"
        verbose_name_plural = "Teritorijų planavimo dokumentai"
        ordering = ["-approved_at", "-scraped_at"]
        indexes = [
            models.Index(fields=["doc_type"]),
            models.Index(fields=["status"]),
            models.Index(fields=["municipality"]),
        ]

    def __str__(self) -> str:
        return f"{self.tpdris_id} — {self.title[:80]}"
