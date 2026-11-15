"""Tests for Lithuanian address → Nominatim query normalization.

Run inside the scraper container (stdlib only, no pytest in that image):
    docker compose exec scraper_worker python -m unittest discover -s tests -v
"""

import unittest

from realestate_spiders.lt_address import geocode_queries, nominative_variants


class NominativeVariantsTest(unittest.TestCase):
    def test_plural_village_locatives(self) -> None:
        self.assertEqual(nominative_variants("Stančiuose"), ["Stančiai"])
        self.assertEqual(nominative_variants("Bikūnuose"), ["Bikūnai"])
        self.assertEqual(nominative_variants("Sakiškėse"), ["Sakiškės"])
        self.assertEqual(nominative_variants("Vėliučionyse"), ["Vėliučionys"])

    def test_singular_town_locatives(self) -> None:
        self.assertEqual(nominative_variants("Kretingoje"), ["Kretinga"])
        self.assertEqual(nominative_variants("Plungėje"), ["Plungė"])
        self.assertEqual(nominative_variants("Vilniuje"), ["Vilnius"])

    def test_nominative_names_are_left_alone(self) -> None:
        for name in ["Kretinga", "Palanga", "Gargždai", "Šventoji", "Nida", "Sakiškės"]:
            self.assertEqual(nominative_variants(name), [name])


class GeocodeQueriesTest(unittest.TestCase):
    # Each expected query was checked by hand against the local Nominatim.
    def test_district_village_street(self) -> None:
        queries = geocode_queries("Klaipėdos r. sav., Stančiuose, Žinių g.")
        self.assertIn("Stančiai, Klaipėdos rajono savivaldybė", queries)

    def test_street_and_village_without_municipality_match(self) -> None:
        queries = geocode_queries("Vilniaus r. sav., Sakiškėse, Debesijos g.")
        self.assertIn("Debesijos g., Sakiškės", queries)

    def test_village_only(self) -> None:
        queries = geocode_queries("Utenos r. sav., Bikūnuose")
        self.assertIn("Bikūnai, Utenos rajono savivaldybė", queries)

    def test_full_rajono_sav_spelling(self) -> None:
        queries = geocode_queries("Vilniaus rajono sav., Skaidiškėse, Tvenkinio g.")
        self.assertIn("Skaidiškės, Vilniaus rajono savivaldybė", queries)

    def test_city_and_district_in_one_part(self) -> None:
        queries = geocode_queries("Vilniuje Kairėnuose, Ližiškių g.")
        self.assertIn("Ližiškių g., Vilnius", queries)

    def test_most_specific_query_comes_first(self) -> None:
        queries = geocode_queries("Klaipėdos r. sav., Stančiuose, Žinių g.")
        self.assertEqual(queries[0], "Žinių g., Stančiai, Klaipėdos rajono savivaldybė")

    def test_original_address_is_last_resort(self) -> None:
        address = "Klaipėdos r. sav., Stančiuose, Žinių g."
        self.assertEqual(geocode_queries(address)[-1], address)

    def test_city_used_when_address_has_no_place(self) -> None:
        self.assertIn("Kretinga", geocode_queries("", city="Kretinga"))

    def test_no_duplicates_or_empties(self) -> None:
        queries = geocode_queries("Utenos r. sav., Bikūnuose")
        self.assertEqual(len(queries), len(set(queries)))
        self.assertNotIn("", queries)


if __name__ == "__main__":
    unittest.main()
