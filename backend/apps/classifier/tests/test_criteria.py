"""criteria.py is pure: no Django, no network. Missing values are unknown, never fail."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.classifier import criteria
from apps.classifier.criteria import Status

KRETINGA = (55.8901, 21.2424)
KARTENA = (55.9204, 21.4739)
KLAIPEDA = (55.7033, 21.1443)
PALANGA = (55.9175, 21.0686)
# On the straight line between Kretinga and Kartena, about halfway.
BETWEEN_KRETINGA_KARTENA = (55.9053, 21.3581)


class TestPrice:
    @pytest.mark.parametrize(
        ("price", "status"),
        [
            (200_000, Status.PASS),
            (250_000, Status.PASS),
            (250_000.01, Status.FAIL),
            (400_000, Status.FAIL),
            (None, Status.UNKNOWN),
            (0, Status.UNKNOWN),
        ],
    )
    def test_limit_is_250k_inclusive(self, price, status):
        assert criteria.check_price(price, "EUR").status == status

    def test_other_currency_is_unknown_not_fail(self):
        assert criteria.check_price(900_000, "PLN").status == Status.UNKNOWN


class TestArea:
    @pytest.mark.parametrize(
        ("area", "status"),
        [
            (94.99, Status.FAIL),
            (95, Status.PASS),
            (100, Status.PASS),
            (120, Status.PASS),
            (120.01, Status.FAIL),
            (None, Status.UNKNOWN),
            (0, Status.UNKNOWN),
        ],
    )
    def test_95_to_120_inclusive(self, area, status):
        assert criteria.check_area(area).status == status


class TestPlot:
    @pytest.mark.parametrize(
        ("plot", "status"),
        [
            (7.99, Status.FAIL),
            (8, Status.PASS),
            (10, Status.PASS),
            (12, Status.PASS),
            (12.01, Status.FAIL),
            (None, Status.UNKNOWN),
        ],
    )
    def test_8_to_12_ares_inclusive(self, plot, status):
        assert criteria.check_plot(plot).status == status


class TestLocation:
    def test_kretinga_centre_passes(self):
        assert criteria.check_location(*KRETINGA).status == Status.PASS

    def test_accepted_village_centre_passes(self):
        assert criteria.check_location(*KARTENA).status == Status.PASS

    def test_point_between_kretinga_and_a_village_passes(self):
        result = criteria.check_location(*BETWEEN_KRETINGA_KARTENA)
        assert result.status == Status.PASS

    @pytest.mark.parametrize("point", [KLAIPEDA, PALANGA])
    def test_other_towns_fail(self, point):
        assert criteria.check_location(*point).status == Status.FAIL

    def test_no_coordinates_and_no_name_is_unknown(self):
        assert criteria.check_location(None, None, texts=[]).status == Status.UNKNOWN

    @pytest.mark.parametrize("precision", ["city", "approximate"])
    def test_approximate_coordinates_are_unknown_not_fail(self, precision):
        result = criteria.check_location(*KLAIPEDA, precision=precision)
        assert result.status == Status.UNKNOWN

    @pytest.mark.parametrize("precision", [None, "", "exact", "street"])
    def test_other_or_missing_precision_uses_the_coordinates(self, precision):
        assert criteria.check_location(*KLAIPEDA, precision=precision).status == Status.FAIL

    @pytest.mark.parametrize(
        "text",
        [
            "Dupulčiai, Kretingos r. sav.",
            "Dupulčiuose, Kretingos raj.",
            "Klonaičiai",
            "Jokūbavas, Žalgirio sen.",
            "Jakubavai",
            "Raguviškių k.",
            "Kartenoje",
            "Kretingos m.",
            "Kretinga, Vilniaus g. 5",
        ],
    )
    def test_accepted_name_passes_without_usable_coordinates(self, text):
        result = criteria.check_location(*KLAIPEDA, texts=[text], precision="city")
        assert result.status == Status.PASS

    def test_name_with_far_reliable_coordinates_is_unknown(self):
        result = criteria.check_location(*KLAIPEDA, texts=["Kretinga"], precision="exact")
        assert result.status == Status.UNKNOWN

    @pytest.mark.parametrize(
        "text",
        [
            "Kretingos r. sav., Salantai",
            "Kartenos sen., Vydmantai",
            "Palanga",
            "Kartenos g. 3, Gargždai",
        ],
    )
    def test_district_and_street_names_do_not_count(self, text):
        result = criteria.check_location(*KLAIPEDA, texts=[text], precision="exact")
        assert result.status == Status.FAIL


class TestTypeKeywords:
    def test_flag_means_new(self):
        assert criteria.keyword_new_build(True, 2010, "", 2026) is True

    @pytest.mark.parametrize("text", ["Naujos statybos namas", "nauja statyba", "NAUJAS NAMAS"])
    def test_new_build_wording(self, text):
        assert criteria.keyword_new_build(False, None, text, 2026) is True

    def test_old_year_means_not_new(self):
        assert criteria.keyword_new_build(False, 1975, "", 2026) is False

    def test_recent_year_with_no_flag_is_unknown(self):
        assert criteria.keyword_new_build(False, 2025, "", 2026) is None

    def test_nothing_is_unknown(self):
        assert criteria.keyword_new_build(False, None, "Namas", 2026) is None

    @pytest.mark.parametrize(
        "text", ["Parduodama namo dalis", "namo dalies pardavimas", "Namo dalį Kretingoje"]
    )
    def test_part_of_house_wording(self, text):
        assert criteria.keyword_part_of_house("house", text) is True

    def test_part_of_house_property_type(self):
        assert criteria.keyword_part_of_house("part_of_house", "") is True

    def test_plain_house_is_not_part_of_house(self):
        assert criteria.keyword_part_of_house("house", "Naujas namas su sklypu") is False


def _results(**overrides):
    base = {
        "price": criteria.check_price(200_000, "EUR"),
        "area": criteria.check_area(100),
        "plot": criteria.check_plot(10),
        "location": criteria.check_location(*KRETINGA),
        "new_build": criteria.type_result(True, "new_build"),
        "whole_house": criteria.type_result(False, "whole_house"),
    }
    base.update(overrides)
    return base


class TestDecide:
    def test_all_pass_is_match_with_high_score(self):
        decision = criteria.decide(_results())
        assert decision.verdict == "match"
        assert 0.8 <= decision.score <= 1.0
        assert decision.failed == [] and decision.unknown == []

    def test_preferred_values_score_higher_than_edge_values(self):
        ideal = criteria.decide(_results())
        edge = criteria.decide(
            _results(
                price=criteria.check_price(250_000, "EUR"),
                area=criteria.check_area(95),
                plot=criteria.check_plot(12),
            )
        )
        assert edge.verdict == "match"
        assert 0.8 <= edge.score < ideal.score

    def test_one_fail_is_skip_with_low_score_and_reason(self):
        decision = criteria.decide(_results(price=criteria.check_price(300_000, "EUR")))
        assert decision.verdict == "skip"
        assert 0.0 <= decision.score <= 0.2
        assert decision.failed == ["price"]
        assert "250" in decision.summary

    def test_unknown_without_fail_is_review(self):
        decision = criteria.decide(_results(plot=criteria.check_plot(None)))
        assert decision.verdict == "review"
        assert 0.4 <= decision.score < 0.8
        assert decision.unknown == ["plot"]

    def test_fail_beats_unknown(self):
        decision = criteria.decide(
            _results(plot=criteria.check_plot(None), area=criteria.check_area(50))
        )
        assert decision.verdict == "skip"

    def test_everything_unknown_is_review_not_skip(self):
        decision = criteria.decide(
            {
                "price": criteria.check_price(None, "EUR"),
                "area": criteria.check_area(None),
                "plot": criteria.check_plot(None),
                "location": criteria.check_location(None, None),
                "new_build": criteria.type_result(None, "new_build"),
                "whole_house": criteria.type_result(None, "whole_house"),
            }
        )
        assert decision.verdict == "review"

    def test_part_of_house_is_always_skip(self):
        decision = criteria.decide(_results(whole_house=criteria.type_result(True, "whole_house")))
        assert decision.verdict == "skip"

    def test_hard_filter_results_omit_unknown_and_merge_type(self):
        decision = criteria.decide(
            _results(
                plot=criteria.check_plot(None),
                new_build=criteria.type_result(False, "new_build"),
            )
        )
        assert decision.hard_filter_results == {
            "price_ok": True,
            "area_ok": True,
            "location_ok": True,
            "type_ok": False,
        }


class TestSingleSource:
    def test_describe_lists_every_limit_and_place(self):
        text = criteria.describe()
        for needle in ("250,000", "95", "120", "8", "12"):
            assert needle in text
        for place in criteria.PLACES:
            assert place.name in text

    def test_prompts_are_built_from_criteria(self):
        from apps.classifier import prompts

        for prompt in (prompts.SYSTEM_PROMPT, prompts.NARRATIVE_SYSTEM_PROMPT):
            assert criteria.describe() in prompt

    def test_every_accepted_place_has_a_pattern(self):
        for place in criteria.PLACES:
            assert place.patterns, place.name

    def test_command_file_points_at_criteria_and_has_no_copy_of_the_lists(self):
        path = Path(__file__).resolve().parents[4] / ".claude" / "commands" / "evaluate.md"
        if not path.exists():
            pytest.skip("repo root is not mounted in this container")
        text = path.read_text(encoding="utf-8")
        assert "apps.classifier.criteria" in text
        for place in criteria.PLACES:
            assert place.name not in text
        assert "250,000" not in text
