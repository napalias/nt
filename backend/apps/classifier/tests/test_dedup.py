from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings as django_settings
from django.contrib.gis.geos import Point
from model_bakery import baker

from apps.classifier import judge, llm
from apps.classifier.dedup import compare_pair, find_candidates, pair_key, run_dedup
from apps.classifier.models import JudgmentLog, ListingCluster
from apps.classifier.tests.helpers import COMPARE_PAYLOAD, make_client, make_response
from apps.listings.models import Listing

POINT = Point(21.2420, 55.8835, srid=4326)
FAR_POINT = Point(21.3420, 55.9835, srid=4326)  # several km away

CITY = Listing.LocationPrecision.CITY
EXACT = Listing.LocationPrecision.EXACT

NOT_SAME_PAYLOAD = {"is_same": False, "confidence": 0.95, "reasoning": "Different houses."}


def _listing(source="domoplius", **kwargs):
    defaults = {
        "source": source,
        "location": POINT,
        "address_raw": "A",
        "city": "Kretinga",
        "rooms": 4,
        "price": Decimal("200000"),
        "area_sqm": Decimal("100"),
        "location_precision": EXACT,
        "is_active": True,
    }
    defaults.update(kwargs)
    return baker.make(Listing, **defaults)


def _pair(**kwargs):
    a = _listing("domoplius", content_hash="hash-a", **kwargs)
    b = _listing("skelbiu", content_hash="hash-b", **kwargs)
    return a, b


def _log_judgment(a, b, decided_by="fallback", **kwargs):
    fields = {
        "request_id": "00000000-0000-0000-0000-000000000001",
        "kind": "dedup",
        "subject_id": pair_key(a, b),
        "question": "same_property",
        "question_type": "noul",
        "mode": "off",
        "decided_by": decided_by,
        "jev_model": "jev-1.14.0",
    }
    fields.update(kwargs)
    return JudgmentLog.objects.create(**fields)


class FakeJev:
    """Stands in for the TypeSafe client: answers every same_property question with P(same)."""

    def __init__(self, probability):
        self.probability = probability
        self.calls = []

    def system_one(self, *, state, questions, model, timeout):
        self.calls.append({"state": state, "questions": questions})
        return {
            "model": model,
            "answers": {"same_property": {"type": "noul", "noul": self.probability}},
            "usage": {"input_tokens": 10, "output_tokens": 1},
        }


@pytest.fixture
def jev_on(settings, monkeypatch):
    """JEV_MODE=on with the default grey band 0.2-0.8 and a fake Jev client factory."""
    settings.JEV_MODE = "on"
    settings.JEV_MODEL = "jev-1.14.0"
    settings.JEV_DEFAULT_THRESHOLD = {"yes": 0.90, "no": 0.10, "min_probability": 0.90}
    settings.JEV_THRESHOLDS = {"same_property": {"yes": 0.8, "no": 0.2}}

    def install(probability):
        fake = FakeJev(probability)
        monkeypatch.setattr(judge, "get_default_client", lambda: fake)
        return fake

    return install


def _claude(payload=COMPARE_PAYLOAD):
    return make_client(make_response(payload))


@pytest.mark.django_db
class TestComparePair:
    @patch("apps.classifier.dedup.get_client")
    def test_uses_fast_model_structured_output(self, mock_get_client, settings):
        settings.CLAUDE_FAST_MODEL = "claude-test-fast"
        client = make_client(make_response(COMPARE_PAYLOAD))
        mock_get_client.return_value = client
        a, b = _pair()

        result = compare_pair(a, b)

        assert result["is_same"] is True
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-test-fast"
        assert "tool_choice" not in kwargs
        assert "tools" not in kwargs
        assert kwargs["output_config"]["format"]["schema"]["additionalProperties"] is False

    @patch("apps.classifier.dedup.get_client")
    def test_truncated_response_raises(self, mock_get_client):
        mock_get_client.return_value = make_client(
            make_response(COMPARE_PAYLOAD, stop_reason="max_tokens")
        )
        a, b = _pair()
        with pytest.raises(llm.TruncatedOutputError):
            compare_pair(a, b)

    @patch("apps.classifier.dedup.get_client")
    def test_out_of_range_confidence_raises(self, mock_get_client):
        mock_get_client.return_value = make_client(
            make_response({**COMPARE_PAYLOAD, "confidence": 4})
        )
        a, b = _pair()
        with pytest.raises(llm.InvalidOutputError):
            compare_pair(a, b)

    def test_missing_api_key_fails_loudly(self, settings):
        settings.ANTHROPIC_API_KEY = ""
        a, b = _pair()
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            compare_pair(a, b)


@pytest.mark.django_db
class TestRunDedup:
    @patch("apps.classifier.dedup.get_client")
    def test_creates_cluster_for_confirmed_match(self, mock_get_client):
        mock_get_client.return_value = make_client(make_response(COMPARE_PAYLOAD))
        _pair()
        clusters = run_dedup()
        assert len(clusters) == 1
        assert ListingCluster.objects.count() == 1

    @patch("apps.classifier.dedup.get_client")
    def test_comparison_failures_are_logged_as_error(self, mock_get_client, caplog):
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("model not found")
        mock_get_client.return_value = client
        _pair()

        with caplog.at_level("ERROR"):
            clusters = run_dedup()

        assert clusters == []
        assert any(
            r.levelname == "ERROR"
            and "RuntimeError" in r.getMessage()
            and "dedup" in r.getMessage()
            for r in caplog.records
        )

    @patch("apps.classifier.dedup.get_client")
    def test_low_confidence_claude_match_is_not_clustered(self, mock_get_client):
        mock_get_client.return_value = _claude({**COMPARE_PAYLOAD, "confidence": 0.5})
        _pair()
        assert run_dedup() == []
        assert ListingCluster.objects.count() == 0


class TestSettingsDefaults:
    def test_pair_cap_defaults_to_200(self):
        assert django_settings.DEDUP_MAX_PAIRS_PER_RUN == 200

    def test_grey_band_defaults_to_0_2_through_0_8(self):
        threshold = judge.threshold_for("same_property")
        assert (threshold.no, threshold.yes) == (0.2, 0.8)


@pytest.mark.django_db
class TestPairKey:
    def test_is_independent_of_order(self):
        a, b = _pair()
        assert pair_key(a, b) == pair_key(b, a)

    def test_fits_the_judgment_log_subject_column(self):
        a, b = _pair()
        a.content_hash = "x" * 64
        b.content_hash = "y" * 64
        assert len(pair_key(a, b)) <= judge.MAX_SUBJECT_LENGTH

    def test_changes_when_either_content_hash_changes(self):
        a, b = _pair()
        before = pair_key(a, b)
        b.content_hash = "hash-b-edited"
        assert pair_key(a, b) != before


@pytest.mark.django_db
class TestPairCache:
    def test_pair_with_a_logged_judgment_is_skipped(self):
        a, b = _pair()
        assert len(find_candidates()) == 1
        _log_judgment(a, b)
        assert find_candidates() == []

    def test_a_judgment_logged_in_the_other_order_still_counts(self):
        a, b = _pair()
        _log_judgment(b, a)
        assert find_candidates() == []

    def test_judgment_from_shadow_and_jev_decisions_count_too(self):
        a, b = _pair()
        _log_judgment(a, b, decided_by="jev", mode="on")
        assert find_candidates() == []

    def test_a_row_where_nobody_decided_does_not_count(self):
        a, b = _pair()
        _log_judgment(a, b, decided_by="none")
        assert len(find_candidates()) == 1

    def test_other_kinds_and_questions_do_not_count(self):
        a, b = _pair()
        _log_judgment(a, b, kind="evaluation")
        _log_judgment(a, b, question="something_else")
        assert len(find_candidates()) == 1

    def test_changed_content_makes_the_pair_fair_game_again(self):
        a, b = _pair()
        _log_judgment(a, b)
        assert find_candidates() == []

        Listing.objects.filter(pk=b.pk).update(content_hash="hash-b-edited")

        assert len(find_candidates()) == 1

    @patch("apps.classifier.dedup.get_client")
    def test_second_run_does_not_pay_for_the_same_not_same_pair(self, mock_get_client):
        client = _claude(NOT_SAME_PAYLOAD)
        mock_get_client.return_value = client
        _pair()

        run_dedup()
        assert client.messages.create.call_count == 1
        run_dedup()
        assert client.messages.create.call_count == 1

    @patch("apps.classifier.dedup.get_client")
    def test_off_mode_records_the_judgment_that_the_cache_reads(self, mock_get_client):
        mock_get_client.return_value = _claude(NOT_SAME_PAYLOAD)
        a, b = _pair()

        run_dedup()

        row = JudgmentLog.objects.get()
        assert (row.kind, row.question, row.mode) == ("dedup", "same_property", "off")
        assert row.subject_id == pair_key(a, b)
        assert row.decided_by == "fallback"
        assert row.fallback_answer is False

    @patch("apps.classifier.dedup.get_client")
    def test_a_failed_comparison_is_retried_next_run(self, mock_get_client):
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("overloaded")
        mock_get_client.return_value = client
        _pair()

        run_dedup()
        run_dedup()

        assert client.messages.create.call_count == 2
        assert JudgmentLog.objects.count() == 0


@pytest.mark.django_db
class TestPairCap:
    @patch("apps.classifier.dedup.get_client")
    def test_at_most_the_capped_number_of_pairs_is_judged(self, mock_get_client, settings):
        settings.DEDUP_MAX_PAIRS_PER_RUN = 2
        client = _claude(NOT_SAME_PAYLOAD)
        mock_get_client.return_value = client
        for i in range(4):
            a = _listing("domoplius", content_hash=f"d{i}", location=Point(21.0 + i, 55.0))
            b = _listing("skelbiu", content_hash=f"s{i}", location=Point(21.0 + i, 55.0))
            assert a.pk and b.pk

        run_dedup()
        assert client.messages.create.call_count == 2

        # The pairs that were not reached last time are picked up next run.
        run_dedup()
        assert client.messages.create.call_count == 4

    @patch("apps.classifier.dedup.get_client")
    def test_cached_pairs_do_not_use_up_the_cap(self, mock_get_client, settings):
        settings.DEDUP_MAX_PAIRS_PER_RUN = 1
        client = _claude(NOT_SAME_PAYLOAD)
        mock_get_client.return_value = client
        a1 = _listing("domoplius", content_hash="d1", location=Point(21.0, 55.0))
        b1 = _listing("skelbiu", content_hash="s1", location=Point(21.0, 55.0))
        _listing("domoplius", content_hash="d2", location=Point(22.0, 55.0))
        _listing("skelbiu", content_hash="s2", location=Point(22.0, 55.0))
        _log_judgment(a1, b1)

        run_dedup()

        assert client.messages.create.call_count == 1


@pytest.mark.django_db
class TestCityPrecision:
    def _city_pair(self, **b_kwargs):
        a = _listing("domoplius", content_hash="a", location_precision=CITY)
        b_fields = {"location_precision": CITY, "content_hash": "b"}
        b_fields.update(b_kwargs)
        b = _listing("skelbiu", **b_fields)
        return a, b

    def test_same_city_price_and_area_within_5_percent_match(self):
        self._city_pair(price=Decimal("208000"), area_sqm=Decimal("104"))
        assert len(find_candidates()) == 1

    def test_the_50m_rule_alone_is_not_enough_for_city_centre_coordinates(self):
        # Both sit on the same city-centre point, but the price is 10% apart.
        self._city_pair(price=Decimal("220000"))
        assert find_candidates() == []

    def test_area_more_than_5_percent_apart_does_not_match(self):
        self._city_pair(area_sqm=Decimal("110"))
        assert find_candidates() == []

    def test_different_city_does_not_match_even_on_the_same_point(self):
        self._city_pair(city="Klaipeda")
        assert find_candidates() == []

    def test_city_match_does_not_need_close_coordinates(self):
        self._city_pair(location=FAR_POINT)
        assert len(find_candidates()) == 1

    def test_missing_price_or_area_does_not_match(self):
        self._city_pair(price=None)
        assert find_candidates() == []

    def test_a_city_listing_never_matches_on_the_50m_rule_with_an_exact_one(self):
        a = _listing("domoplius", content_hash="a", location_precision=CITY)
        _listing("skelbiu", content_hash="b", location_precision=EXACT, price=Decimal("300000"))
        assert a.pk
        assert find_candidates() == []

    def test_city_listing_matches_an_exact_one_in_the_same_city_by_price_and_area(self):
        _listing("domoplius", content_hash="a", location_precision=CITY)
        _listing("skelbiu", content_hash="b", location_precision=EXACT, location=FAR_POINT)
        assert len(find_candidates()) == 1

    def test_exact_listings_still_use_the_50m_rule(self):
        _pair(price=Decimal("200000"))
        _listing("aruodas", content_hash="far", location=FAR_POINT)
        assert len(find_candidates()) == 1


@pytest.mark.django_db
class TestClusterMerge:
    def _cluster(self, *listings, confidence=0.8):
        cluster = baker.make(ListingCluster, confidence=confidence, reasoning="r")
        cluster.listings.add(*listings)
        return cluster

    @patch("apps.classifier.dedup.get_client")
    def test_two_existing_clusters_merge_into_the_older_one(self, mock_get_client):
        mock_get_client.return_value = _claude(
            {"is_same": True, "confidence": 0.95, "reasoning": "same"}
        )
        a, b = _pair()
        a2 = _listing("aruodas", content_hash="a2", location=FAR_POINT, address_raw="elsewhere")
        b2 = _listing("aruodas", content_hash="b2", location=FAR_POINT, address_raw="x")
        older = self._cluster(a, a2, confidence=0.8)
        newer = self._cluster(b, b2, confidence=0.7)
        assert older.pk < newer.pk

        run_dedup()

        assert list(ListingCluster.objects.values_list("pk", flat=True)) == [older.pk]
        merged = ListingCluster.objects.get(pk=older.pk)
        assert set(merged.listings.values_list("pk", flat=True)) == {a.pk, a2.pk, b.pk, b2.pk}
        assert merged.confidence == pytest.approx(0.95)

    @patch("apps.classifier.dedup.get_client")
    def test_the_older_cluster_survives_whichever_side_it_is_on(self, mock_get_client):
        mock_get_client.return_value = _claude()
        a, b = _pair()
        first = self._cluster(b)
        second = self._cluster(a)
        assert first.pk < second.pk

        run_dedup()

        assert list(ListingCluster.objects.values_list("pk", flat=True)) == [first.pk]
        assert set(ListingCluster.objects.get().listings.all()) == {a, b}

    @patch("apps.classifier.dedup.get_client")
    def test_merge_keeps_the_survivors_canonical(self, mock_get_client):
        mock_get_client.return_value = _claude()
        a, b = _pair()
        older = self._cluster(a)
        older.canonical = a
        older.save()
        self._cluster(b)

        run_dedup()

        assert ListingCluster.objects.get().canonical == a

    @patch("apps.classifier.dedup.get_client")
    def test_a_listing_joins_the_existing_cluster_of_its_partner(self, mock_get_client):
        mock_get_client.return_value = _claude()
        a, b = _pair()
        cluster = self._cluster(a)

        created = run_dedup()

        assert created == []
        assert set(ListingCluster.objects.get(pk=cluster.pk).listings.all()) == {a, b}

    @patch("apps.classifier.dedup.get_client")
    def test_pair_already_in_one_cluster_is_left_alone(self, mock_get_client):
        client = _claude()
        mock_get_client.return_value = client
        a, b = _pair()
        self._cluster(a, b)

        run_dedup()

        assert client.messages.create.call_count == 0
        assert ListingCluster.objects.count() == 1


@pytest.mark.django_db
class TestJevAndTheGreyBand:
    @patch("apps.classifier.dedup.get_client")
    def test_jev_confident_yes_decides_without_asking_claude(self, mock_get_client, jev_on):
        fake = jev_on(0.95)
        client = _claude(NOT_SAME_PAYLOAD)  # Claude would disagree; it must not be asked
        mock_get_client.return_value = client
        _pair()

        clusters = run_dedup()

        assert len(clusters) == 1
        assert client.messages.create.call_count == 0
        assert len(fake.calls) == 1
        assert clusters[0].confidence == pytest.approx(0.95)

    @patch("apps.classifier.dedup.get_client")
    def test_jev_confident_no_decides_without_asking_claude(self, mock_get_client, jev_on):
        jev_on(0.05)
        client = _claude()  # Claude would say same; it must not be asked
        mock_get_client.return_value = client
        _pair()

        assert run_dedup() == []

        assert client.messages.create.call_count == 0
        assert ListingCluster.objects.count() == 0

    @pytest.mark.parametrize("probability", [0.21, 0.5, 0.79])
    @patch("apps.classifier.dedup.get_client")
    def test_inside_the_grey_band_claude_decides(self, mock_get_client, jev_on, probability):
        jev_on(probability)
        client = _claude(NOT_SAME_PAYLOAD)
        mock_get_client.return_value = client
        _pair()

        clusters = run_dedup()

        assert client.messages.create.call_count == 1
        assert clusters == []
        assert client.messages.create.call_args.kwargs["model"] == django_settings.CLAUDE_FAST_MODEL

    @patch("apps.classifier.dedup.get_client")
    def test_inside_the_grey_band_claude_can_confirm_a_match(self, mock_get_client, jev_on):
        jev_on(0.5)
        mock_get_client.return_value = _claude()
        _pair()

        assert len(run_dedup()) == 1

    @patch("apps.classifier.dedup.get_client")
    def test_band_bounds_come_from_settings(self, mock_get_client, jev_on, settings):
        jev_on(0.9)
        settings.JEV_THRESHOLDS = {"same_property": {"yes": 0.95, "no": 0.05}}
        client = _claude(NOT_SAME_PAYLOAD)
        mock_get_client.return_value = client
        _pair()

        run_dedup()

        assert client.messages.create.call_count == 1  # 0.9 is now inside the band

    @patch("apps.classifier.dedup.get_client")
    def test_shadow_mode_claude_decides_and_jev_is_only_logged(
        self, mock_get_client, jev_on, settings
    ):
        jev_on(0.95)
        settings.JEV_MODE = "shadow"
        client = _claude(NOT_SAME_PAYLOAD)
        mock_get_client.return_value = client
        a, b = _pair()

        clusters = run_dedup()

        assert clusters == []
        assert client.messages.create.call_count == 1
        row = JudgmentLog.objects.get()
        assert row.mode == "shadow"
        assert row.subject_id == pair_key(a, b)
        assert row.jev_answer is True
        assert row.fallback_answer is False
        assert row.decided_by == "fallback"

    @patch("apps.classifier.dedup.get_client")
    def test_jev_decision_is_logged_and_cached_for_the_next_run(self, mock_get_client, jev_on):
        fake = jev_on(0.05)
        mock_get_client.return_value = _claude()
        _pair()

        run_dedup()
        run_dedup()

        assert len(fake.calls) == 1
        row = JudgmentLog.objects.get()
        assert (row.kind, row.question, row.decided_by) == ("dedup", "same_property", "jev")

    @patch("apps.classifier.dedup.get_client")
    def test_jev_sees_both_listings_and_no_contact_data(self, mock_get_client, jev_on):
        fake = jev_on(0.95)
        mock_get_client.return_value = _claude()
        a = _listing(
            "domoplius", content_hash="a", title="Namas A", description="Skambinti +37061234567"
        )
        _listing("skelbiu", content_hash="b", title="Namas B")
        assert a.pk

        run_dedup()

        state = fake.calls[0]["state"]
        assert state["a"]["title"] == "Namas A"
        assert state["b"]["title"] == "Namas B"
        assert "61234567" not in str(state)

    @patch("apps.classifier.dedup.get_client")
    def test_jev_failure_falls_back_to_claude(self, mock_get_client, settings, monkeypatch):
        settings.JEV_MODE = "on"

        class Broken:
            def system_one(self, **_):
                raise ConnectionError("down")

        monkeypatch.setattr(judge, "get_default_client", lambda: Broken())
        mock_get_client.return_value = _claude()
        _pair()

        assert len(run_dedup()) == 1
