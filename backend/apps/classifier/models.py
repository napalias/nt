from django.db import models

from apps.listings.models import Listing


class ListingEvaluation(models.Model):
    class Verdict(models.TextChoices):
        MATCH = "match", "Tinka"
        REVIEW = "review", "Peržiūrėti"
        SKIP = "skip", "Praleisti"

    listing = models.OneToOneField(Listing, on_delete=models.CASCADE, related_name="evaluation")
    verdict = models.CharField(
        max_length=8, choices=Verdict.choices, db_index=True, verbose_name="Verdiktas"
    )
    match_score = models.FloatField(verbose_name="Atitikimo balas")
    summary = models.TextField(verbose_name="AI santrauka")
    hard_filter_results = models.JSONField(default=dict, verbose_name="Filtrų rezultatai")
    quality_notes = models.JSONField(default=list, verbose_name="Kokybės pastabos")
    red_flags = models.JSONField(default=list, verbose_name="Raudonos vėliavos")
    classified_at = models.DateTimeField(auto_now=True, verbose_name="Klasifikuota")
    model_used = models.CharField(max_length=64, verbose_name="Naudotas modelis")

    class Meta:
        verbose_name = "Skelbimo vertinimas"
        verbose_name_plural = "Skelbimų vertinimai"
        ordering = ["-match_score"]

    def __str__(self) -> str:
        return f"{self.listing} → {self.verdict} ({self.match_score:.0%})"


class UserFeedback(models.Model):
    class FeedbackType(models.TextChoices):
        LIKE = "like", "Patinka"
        DISLIKE = "dislike", "Nepatinka"

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="feedback")
    feedback_type = models.CharField(
        max_length=8, choices=FeedbackType.choices, verbose_name="Tipas"
    )
    reason = models.TextField(verbose_name="Priežastis")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Sukurta")

    class Meta:
        verbose_name = "Vartotojo atsiliepimas"
        verbose_name_plural = "Vartotojo atsiliepimai"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.feedback_type}: {self.listing} — {self.reason[:60]}"


class LearnedPreference(models.Model):
    class PreferenceType(models.TextChoices):
        LIKE = "like", "Prioritizuoti"
        DISLIKE = "dislike", "Vengti"

    preference_type = models.CharField(
        max_length=8, choices=PreferenceType.choices, verbose_name="Tipas"
    )
    pattern = models.TextField(verbose_name="Šablonas")
    weight = models.FloatField(default=1.0, verbose_name="Svoris")
    source_feedback = models.ForeignKey(
        UserFeedback,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="extracted_preferences",
        verbose_name="Šaltinio atsiliepimas",
    )
    is_active = models.BooleanField(default=True, db_index=True, verbose_name="Aktyvus")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Sukurta")

    class Meta:
        verbose_name = "Išmoktas prioritetas"
        verbose_name_plural = "Išmokti prioritetai"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"[{self.preference_type}] {self.pattern[:80]}"


class ListingCluster(models.Model):
    """A group of listings from different portals that represent the same property."""

    listings = models.ManyToManyField(
        Listing, related_name="clusters", verbose_name="Skelbimai klasteryje"
    )
    confidence = models.FloatField(verbose_name="AI pasitikėjimas")
    reasoning = models.TextField(verbose_name="AI paaiškinimas")
    canonical = models.ForeignKey(
        Listing,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="canonical_for",
        verbose_name="Pagrindinis skelbimas",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Sukurta")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Atnaujinta")

    class Meta:
        verbose_name = "Skelbimų klasteris"
        verbose_name_plural = "Skelbimų klasteriai"
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        count = self.listings.count()
        return f"Cluster #{self.pk} ({count} listings, {self.confidence:.0%})"
