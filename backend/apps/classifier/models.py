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


class JudgmentLog(models.Model):
    """One row per question for every judge.ask() call in shadow or on mode.

    Rows of one Jev request share ``request_id``. Token counts and Jev latency belong to the
    whole request and are repeated on each of its rows: sum them over distinct request_id.
    """

    class DecidedBy(models.TextChoices):
        JEV = "jev", "Jev"
        FALLBACK = "fallback", "Atsarginis sprendėjas"
        NONE = "none", "Niekas"

    request_id = models.UUIDField(db_index=True, verbose_name="Užklausos ID")
    kind = models.CharField(max_length=32, verbose_name="Sprendimo rūšis")
    subject_id = models.CharField(max_length=64, verbose_name="Objekto ID")
    question = models.CharField(max_length=64, verbose_name="Klausimas")
    question_type = models.CharField(max_length=8, verbose_name="Klausimo tipas")
    mode = models.CharField(max_length=8, verbose_name="Režimas")
    decided_by = models.CharField(max_length=8, choices=DecidedBy.choices, verbose_name="Nusprendė")
    jev_answer = models.JSONField(null=True, blank=True, verbose_name="Jev atsakymas")
    jev_probability = models.FloatField(null=True, blank=True, verbose_name="Jev tikimybė")
    jev_detail = models.JSONField(default=dict, blank=True, verbose_name="Jev atsakymo detalės")
    jev_error = models.TextField(blank=True, verbose_name="Jev klaida")
    threshold = models.JSONField(default=dict, verbose_name="Slenkstis")
    fallback_answer = models.JSONField(
        null=True, blank=True, verbose_name="Atsarginio sprendėjo atsakymas"
    )
    fallback_error = models.TextField(blank=True, verbose_name="Atsarginio sprendėjo klaida")
    agreement = models.BooleanField(null=True, blank=True, verbose_name="Sutapo")
    jev_model = models.CharField(max_length=64, verbose_name="Jev modelis (prašytas)")
    jev_model_answered = models.CharField(
        max_length=64, blank=True, verbose_name="Jev modelis (atsakęs)"
    )
    fallback_model = models.CharField(
        max_length=64, blank=True, verbose_name="Atsarginio sprendėjo modelis"
    )
    input_tokens = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Įvesties žetonai (visos užklausos)"
    )
    output_tokens = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Išvesties žetonai (visos užklausos)"
    )
    latency_ms = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Jev trukmė, ms (visos užklausos)"
    )
    fallback_latency_ms = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="Atsarginio sprendėjo trukmė, ms"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Sukurta")

    class Meta:
        verbose_name = "Jev sprendimo įrašas"
        verbose_name_plural = "Jev sprendimų įrašai"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["kind", "question", "created_at"]),
            models.Index(fields=["kind", "subject_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.subject_id} {self.question} → {self.decided_by}"
