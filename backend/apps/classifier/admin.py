from django.contrib import admin

from apps.classifier.models import (
    LearnedPreference,
    ListingCluster,
    ListingEvaluation,
    UserFeedback,
)


@admin.register(ListingEvaluation)
class ListingEvaluationAdmin(admin.ModelAdmin):
    list_display = [
        "listing",
        "verdict",
        "match_score",
        "classified_at",
        "model_used",
    ]
    list_filter = ["verdict", "model_used"]
    search_fields = ["listing__title", "summary"]
    readonly_fields = ["classified_at"]


@admin.register(UserFeedback)
class UserFeedbackAdmin(admin.ModelAdmin):
    list_display = ["listing", "feedback_type", "reason", "created_at"]
    list_filter = ["feedback_type"]
    search_fields = ["listing__title", "reason"]
    readonly_fields = ["created_at"]


@admin.register(LearnedPreference)
class LearnedPreferenceAdmin(admin.ModelAdmin):
    list_display = [
        "preference_type",
        "pattern",
        "weight",
        "is_active",
        "created_at",
    ]
    list_filter = ["preference_type", "is_active"]
    search_fields = ["pattern"]
    list_editable = ["is_active", "weight"]


@admin.register(ListingCluster)
class ListingClusterAdmin(admin.ModelAdmin):
    list_display = ["id", "confidence", "canonical", "created_at", "listing_count"]
    readonly_fields = ["created_at", "updated_at"]
    filter_horizontal = ["listings"]

    def listing_count(self, obj):
        return obj.listings.count()

    listing_count.short_description = "Skelbimų sk."
