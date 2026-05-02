from django.db import models


class Document(models.Model):
    """Dokumentas — PDF / failas saugojimui ir teksto ištraukimui."""

    url = models.URLField(verbose_name="Šaltinio URL")
    storage_path = models.CharField(max_length=500, blank=True, verbose_name="Saugojimo kelias")
    title = models.CharField(max_length=500, verbose_name="Pavadinimas")
    extracted_text = models.TextField(blank=True, verbose_name="Ištrauktas tekstas")
    file_size = models.IntegerField(null=True, blank=True, verbose_name="Failo dydis (baitais)")
    content_hash = models.CharField(
        max_length=64, blank=True, db_index=True, verbose_name="Turinio maišos kodas"
    )
    extracted_at = models.DateTimeField(null=True, blank=True, verbose_name="Tekstas ištrauktas")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Sukurta")

    class Meta:
        verbose_name = "Dokumentas"
        verbose_name_plural = "Dokumentai"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title or self.url
