import pytest
from django.contrib.gis.geos import Point
from django.db import IntegrityError
from model_bakery import baker

from apps.classifier.models import LearnedPreference, ListingEvaluation, UserFeedback
from apps.listings.models import Listing


@pytest.mark.django_db
class TestListingEvaluation:
    def test_create_evaluation(self):
        listing = baker.make(
            Listing,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Kretinga",
        )
        evaluation = ListingEvaluation.objects.create(
            listing=listing,
            verdict=ListingEvaluation.Verdict.MATCH,
            match_score=0.85,
            summary="Puikus namas, atitinka visus kriterijus.",
            hard_filter_results={
                "price_ok": True,
                "type_ok": True,
                "area_ok": True,
                "plot_ok": True,
                "location_ok": True,
            },
            quality_notes=["Good photos", "Complete description"],
            red_flags=[],
            model_used="claude-sonnet-4-20250514",
        )
        assert evaluation.pk is not None
        assert evaluation.verdict == "match"
        assert evaluation.match_score == 0.85

    def test_one_evaluation_per_listing(self):
        listing = baker.make(
            Listing,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Kretinga",
        )
        ListingEvaluation.objects.create(
            listing=listing,
            verdict="match",
            match_score=0.9,
            summary="Good",
            model_used="test",
        )
        with pytest.raises(IntegrityError):
            ListingEvaluation.objects.create(
                listing=listing,
                verdict="skip",
                match_score=0.1,
                summary="Bad",
                model_used="test",
            )

    def test_ordering_by_score(self):
        for score in [0.3, 0.9, 0.6]:
            listing = baker.make(
                Listing,
                location=Point(21.2420, 55.8835, srid=4326),
                address_raw="Test",
            )
            ListingEvaluation.objects.create(
                listing=listing,
                verdict="review",
                match_score=score,
                summary="Test",
                model_used="test",
            )
        scores = list(ListingEvaluation.objects.values_list("match_score", flat=True))
        assert scores == [0.9, 0.6, 0.3]


@pytest.mark.django_db
class TestUserFeedback:
    def test_create_feedback(self):
        listing = baker.make(
            Listing,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Test",
        )
        feedback = UserFeedback.objects.create(
            listing=listing,
            feedback_type=UserFeedback.FeedbackType.LIKE,
            reason="Great layout, large windows",
        )
        assert feedback.pk is not None
        assert feedback.feedback_type == "like"

    def test_multiple_feedback_per_listing(self):
        listing = baker.make(
            Listing,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Test",
        )
        UserFeedback.objects.create(listing=listing, feedback_type="like", reason="Nice")
        UserFeedback.objects.create(listing=listing, feedback_type="dislike", reason="Changed mind")
        assert listing.feedback.count() == 2


@pytest.mark.django_db
class TestLearnedPreference:
    def test_create_preference(self):
        pref = LearnedPreference.objects.create(
            preference_type=LearnedPreference.PreferenceType.DISLIKE,
            pattern="Avoids plots narrower than 20m",
            weight=1.5,
        )
        assert pref.pk is not None
        assert pref.is_active is True

    def test_deactivate_preference(self):
        pref = LearnedPreference.objects.create(
            preference_type="like",
            pattern="Prefers south-facing windows",
        )
        pref.is_active = False
        pref.save()
        active = LearnedPreference.objects.filter(is_active=True)
        assert active.count() == 0

    def test_linked_to_feedback(self):
        listing = baker.make(
            Listing,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Test",
        )
        feedback = UserFeedback.objects.create(
            listing=listing,
            feedback_type="dislike",
            reason="Too close to road",
        )
        pref = LearnedPreference.objects.create(
            preference_type="dislike",
            pattern="Avoids proximity to main roads",
            source_feedback=feedback,
        )
        assert pref.source_feedback == feedback
        assert feedback.extracted_preferences.count() == 1
