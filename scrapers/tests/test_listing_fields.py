"""Small value parsers shared by the alio and skelbiu spiders."""

import unittest

from realestate_spiders.spiders.listings.fields import ares, lt_point, number, whole_number, year


class FieldsTest(unittest.TestCase):
    def test_number_reads_lithuanian_decimals_and_thousands(self) -> None:
        self.assertEqual(number("66,39 m²"), 66.39)
        self.assertEqual(number("209 000 €"), 209000.0)
        self.assertEqual(number("209 000 €"), 209000.0)
        self.assertEqual(number("2 512,62 €/m²"), 2512.62)
        self.assertEqual(number(234), 234.0)
        self.assertIsNone(number(""))
        self.assertIsNone(number(None))
        self.assertIsNone(number("nenurodyta"))

    def test_whole_number(self) -> None:
        self.assertEqual(whole_number("3 kamb."), 3)
        self.assertIsNone(whole_number("-"))

    def test_ares_converts_hectares(self) -> None:
        self.assertEqual(ares("15,80 a"), 15.8)
        self.assertEqual(ares("9 a"), 9.0)
        self.assertEqual(ares("0,25 ha"), 25.0)
        self.assertIsNone(ares(""))

    def test_year_is_the_first_plausible_year(self) -> None:
        self.assertEqual(year("1982, 2024 renovuotas"), 1982)
        self.assertEqual(year("2018"), 2018)
        self.assertIsNone(year("12345"))
        self.assertIsNone(year(""))

    def test_lt_point_rejects_values_outside_lithuania(self) -> None:
        self.assertEqual(lt_point("55.88", "21.23"), (55.88, 21.23))
        self.assertIsNone(lt_point("0", "0"))
        self.assertIsNone(lt_point("", ""))
        self.assertIsNone(lt_point(None, "21"))
        self.assertIsNone(lt_point("48.8", "2.3"))


if __name__ == "__main__":
    unittest.main()
