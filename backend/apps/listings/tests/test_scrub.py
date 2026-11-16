"""Contact-data scrub rules (apps.listings.scrub). All phone numbers here are fake."""

import json

import pytest

from apps.listings.scrub import (
    MAX_RAW_DATA_BYTES,
    TRUNCATED_KEY,
    is_contact_key,
    scrub_raw_data,
    scrub_text,
)


class TestScrubText:
    @pytest.mark.parametrize(
        "text",
        [
            "Skambinkite +370 600 00001 dabar",
            "Skambinkite +37060000001 dabar",
            "Skambinkite +370-600-00001 dabar",
            "Skambinkite 00370 600 00001 dabar",
            "Skambinkite 370 600 00001 dabar",
            "Skambinkite 8 600 00001 dabar",
            "Skambinkite 860000001 dabar",
            "Skambinkite 8-600-00001 dabar",
            "Skambinkite (8 5) 200 0004 dabar",
            "Skambinkite +370 (5) 200 0004 dabar",
            "Skambinkite +370 dabar",
        ],
    )
    def test_phone_numbers_removed(self, text):
        out = scrub_text(text)
        assert "370" not in out
        assert not any(c.isdigit() for c in out)
        assert out.startswith("Skambinkite")
        assert out.endswith("dabar")
        assert "  " not in out

    def test_email_removed(self):
        out = scrub_text("Rašykite pardavejas@example.lt arba Jonas.J+nt@mail.example.com ačiū")
        assert "@" not in out
        assert "ačiū" in out

    def test_label_removed_with_number(self):
        assert scrub_text("Parduodamas namas. Tel. +370 600 00001") == "Parduodamas namas."
        assert scrub_text("Mob.: 8 600 00001, namas") == "namas"

    def test_facts_survive(self):
        text = (
            "Namas 2015 m., 120 kv.m, 12 arų sklypas, kaina 125 000 EUR, 8 kambariai, "
            "kadastro nr. 4400-1234-5678-0012. Energijos klasė B."
        )
        assert scrub_text(text) == text

    def test_word_containing_tel_is_not_a_label(self):
        assert scrub_text("Viešbutis hotel 8 600 00001") == "Viešbutis hotel"

    def test_idempotent(self):
        once = scrub_text("Namas, tel. +370 600 00001, a@b.lt, 8 600 00002.\nKita eilutė")
        assert scrub_text(once) == once

    def test_none_and_empty(self):
        assert scrub_text(None) == ""
        assert scrub_text("") == ""

    def test_line_breaks_kept(self):
        assert scrub_text("Pirma\n+370 600 00001\nAntra") == "Pirma\n\nAntra"


class TestContactKeys:
    @pytest.mark.parametrize(
        "key",
        [
            "contact",
            "Contact",
            "sellerName",
            "seller",
            "phone",
            "phoneNumber",
            "email",
            "e-mail",
            "telefonas",
            "mobile",
            "kontaktai",
            "pardavėjas",
            "tel",
            "agent",
        ],
    )
    def test_contact_keys(self, key):
        assert is_contact_key(key)

    @pytest.mark.parametrize("key", ["price", "namo tipas", "hotel", "heating", "title", "area"])
    def test_other_keys(self, key):
        assert not is_contact_key(key)


class TestScrubRawData:
    def test_contact_keys_dropped_at_every_depth(self):
        data = {
            "price": "125000",
            "contact": {"phone": "+370 600 00001", "name": "Jonas"},
            "listing": {"id": 7, "seller": {"email": "a@b.lt"}, "phones": ["+370 600 00002"]},
            "list": [{"phone": "+370 600 00003", "rooms": 4}],
        }
        out = scrub_raw_data(data)
        assert out == {"price": "125000", "listing": {"id": 7}, "list": [{"rooms": 4}]}

    def test_numbers_in_string_values_removed(self):
        embedded = json.dumps({"x": {"mobile": "+370 600 00004"}, "note": "tel +370 600 00005"})
        out = scrub_raw_data({"page_json": embedded, "info": "skambinti 8 600 00006"})
        assert "+370" not in json.dumps(out, ensure_ascii=False)
        assert "600 000" not in json.dumps(out, ensure_ascii=False)

    def test_size_capped_largest_keys_dropped_first(self):
        data = {"small": "keep me", "big": "x" * 50_000, "medium": "y" * 2_000}
        out = scrub_raw_data(data)
        assert len(json.dumps(out, ensure_ascii=False).encode()) <= MAX_RAW_DATA_BYTES
        assert out["small"] == "keep me"
        assert out["medium"] == "y" * 2_000
        assert "big" not in out
        assert out[TRUNCATED_KEY] == ["big"]

    def test_within_cap_untouched(self):
        data = {"a": 1, "b": "two"}
        assert scrub_raw_data(data) == data

    def test_idempotent(self):
        data = {
            "contact": {"phone": "+370 600 00001"},
            "big": "z" * 40_000,
            "info": "tel. 8 600 00002",
            "keep": "value",
        }
        once = scrub_raw_data(data)
        assert scrub_raw_data(once) == once

    def test_non_dict_becomes_empty(self):
        assert scrub_raw_data(None) == {}
        assert scrub_raw_data("text") == {}
