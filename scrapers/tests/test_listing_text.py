"""The text helpers the agency spiders share (numbers, contact-free descriptions)."""

import unittest

from realestate_spiders.spiders.listings.text import clean_lines, to_number, with_sav


class ToNumberTest(unittest.TestCase):
    def test_reads_prices_areas_and_comma_decimals(self) -> None:
        self.assertEqual(to_number("74000 €"), 74000)
        self.assertEqual(to_number("79 000 €"), 79000)  # a space is a thousands separator
        self.assertEqual(to_number("1 250 000 EUR"), 1250000)
        self.assertEqual(to_number("21,68 a"), 21.68)
        self.assertEqual(to_number("231.23 kv. m."), 231.23)
        self.assertEqual(to_number(5), 5)

    def test_no_number_is_none(self) -> None:
        for value in (None, "", "Kaina sutartinė", True):
            self.assertIsNone(to_number(value))

    def test_zero_is_zero_so_callers_can_treat_it_as_no_price(self) -> None:
        self.assertEqual(to_number("0 €"), 0)


class CleanLinesTest(unittest.TestCase):
    def test_drops_contact_lines_and_the_agents_name_and_title(self) -> None:
        lines = [
            "Parduodamas namas.",
            "",
            "Šildymas dujinis.",
            "_______________",
            "Agentas Pavardenis",
            "Nekilnojamojo turto ekspertė",
            "El.p.: agent@example.invalid",
            "Mob.: +370 600 00000",
            "www.example.invalid",
        ]
        self.assertEqual(clean_lines(lines), "Parduodamas namas.\n\nŠildymas dujinis.")

    def test_drops_call_me_sentences(self) -> None:
        lines = [
            "Namas su sklypu.",
            "Dėl išsamesnės informacijos kreipkitės telefonu!",
            "Skambinkite",
        ]
        self.assertEqual(clean_lines(lines), "Namas su sklypu.")

    def test_removes_a_number_or_email_inside_a_kept_line(self) -> None:
        text = clean_lines(["Namas Kretingoje, +370 600 00000, arba agent@example.invalid, geras."])
        self.assertNotIn("370", text)
        self.assertNotIn("@", text)
        self.assertIn("Namas Kretingoje", text)

    def test_keeps_ordinary_lines_that_start_like_a_label(self) -> None:
        lines = [
            "Elektra – yra",
            "Telefono linija įvesta",
            "Teritorija aptverta",
            "Mobili pertvara",
        ]
        self.assertEqual(
            clean_lines(lines),
            "Elektra – yra\nTelefono linija įvesta\nTeritorija aptverta\nMobili pertvara",
        )

    def test_a_name_without_a_job_title_is_not_guessed_away(self) -> None:
        self.assertEqual(
            clean_lines(["Namas parduodamas", "Vilniaus Gatvė"]),
            "Namas parduodamas\nVilniaus Gatvė",
        )

    def test_all_contact_is_an_empty_description(self) -> None:
        self.assertEqual(clean_lines(["Tel. +370 600 00000", "kreipkitės", ""]), "")


class WithSavTest(unittest.TestCase):
    def test_districts_get_their_municipality_suffix(self) -> None:
        self.assertEqual(with_sav("Kretingos r."), "Kretingos r. sav.")
        self.assertEqual(with_sav("Kretinga"), "Kretinga")
        self.assertEqual(with_sav("Palanga"), "Palanga")


if __name__ == "__main__":
    unittest.main()
