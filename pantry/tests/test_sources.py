"""How the source modules talk to their APIs.

Endpoint choice is tested because getting it wrong fails silently. Open Food
Facts' /api/v2/search is filter-based: it ignores an unrecognised parameter
instead of rejecting it, so asking it for `search_terms=ground beef` returns
a valid, cheerful page of nothing to do with beef. Nothing raises, nothing
logs, and the food just isn't there.
"""

from unittest.mock import patch

from django.test import TestCase

from pantry.modules.fdc import FoodDataCentralModule
from pantry.modules.off import (
    LEGACY_SEARCH_URL,
    SEARCHALICIOUS_URL,
    OpenFoodFactsModule,
)
from pantry.modules.base import SourceError

BEEF = {
    "code": "123",
    "product_name": "Ground beef 20% fat",
    "nutriments": {"energy-kcal_100g": 250, "proteins_100g": 17},
}


class OpenFoodFactsSearchTests(TestCase):
    def setUp(self):
        self.module = OpenFoodFactsModule()

    def test_text_search_never_goes_to_the_filter_only_endpoint(self):
        with patch.object(self.module, "_post", return_value={"hits": [BEEF]}) as post:
            with patch.object(self.module, "_get") as get:
                self.module.search_by_name("ground beef")
        self.assertEqual(post.call_args.args[0], SEARCHALICIOUS_URL)
        get.assert_not_called()

    def test_the_query_is_sent_as_q_not_search_terms(self):
        with patch.object(self.module, "_post", return_value={"hits": [BEEF]}) as post:
            self.module.search_by_name("ground beef")
        payload = post.call_args.args[1]
        self.assertEqual(payload["q"], "ground beef")
        self.assertNotIn("search_terms", payload)

    def test_results_come_back_normalised(self):
        with patch.object(self.module, "_post", return_value={"hits": [BEEF]}):
            found = self.module.search_by_name("ground beef")
        self.assertEqual(len(found), 1)
        self.assertIn("beef", found[0].name.lower())

    def test_it_falls_back_to_the_v1_route_when_the_new_one_fails(self):
        with patch.object(self.module, "_post", side_effect=SourceError("OFF", "down")):
            with patch.object(self.module, "_get", return_value={"products": [BEEF]}) as get:
                found = self.module.search_by_name("ground beef")
        self.assertEqual(get.call_args.args[0], LEGACY_SEARCH_URL)
        self.assertEqual(get.call_args.args[1]["search_terms"], "ground beef")
        self.assertEqual(len(found), 1)

    def test_it_falls_back_when_the_new_one_finds_nothing(self):
        with patch.object(self.module, "_post", return_value={"hits": []}):
            with patch.object(self.module, "_get", return_value={"products": [BEEF]}):
                self.assertEqual(len(self.module.search_by_name("ground beef")), 1)

    def test_both_result_keys_are_accepted(self):
        for key in ("hits", "products"):
            with self.subTest(key=key):
                with patch.object(self.module, "_post", return_value={key: [BEEF]}):
                    self.assertEqual(len(self.module.search_by_name("beef")), 1)

    def test_genuinely_no_results_is_an_empty_list_not_an_error(self):
        with patch.object(self.module, "_post", return_value={"hits": []}):
            with patch.object(self.module, "_get", return_value={"products": []}):
                self.assertEqual(self.module.search_by_name("asdfqwer"), [])

    def test_an_empty_query_does_not_hit_the_network(self):
        with patch.object(self.module, "_post") as post:
            with patch.object(self.module, "_get") as get:
                self.assertEqual(self.module.search_by_name("   "), [])
        post.assert_not_called()
        get.assert_not_called()

    def test_barcode_lookup_still_uses_the_v2_product_endpoint(self):
        with patch.object(self.module, "_get", return_value={"status": 1, "product": BEEF}) as get:
            self.module.search_by_barcode("123")
        self.assertIn("/api/v2/product/", get.call_args.args[0])


class FoodDataCentralSearchTests(TestCase):
    def test_fdc_text_search_uses_its_own_search_endpoint(self):
        module = FoodDataCentralModule()
        with patch.object(module, "_get", return_value={"foods": []}) as get:
            module.search_by_name("ground beef", api_key="x")
        self.assertIn("/foods/search", get.call_args.args[0])
        self.assertEqual(get.call_args.args[1]["query"], "ground beef")


class FieldShapeTests(TestCase):
    """Endpoints disagree about the shape of the same field.

    The product route gives `brands` as "Tesco,Tesco Finest"; Search-a-licious
    indexes it as a list. Calling .split on the list was an AttributeError on
    the search page — a live search only, so no amount of mocked routing
    caught it.
    """

    def setUp(self):
        self.module = OpenFoodFactsModule()

    def normalize(self, **payload):
        payload.setdefault("code", "123")
        payload.setdefault("product_name", "Ground beef")
        return self.module.normalize(payload)

    def test_brands_as_a_comma_joined_string(self):
        self.assertEqual(self.normalize(brands="Tesco,Tesco Finest").brand, "Tesco")

    def test_brands_as_a_list(self):
        self.assertEqual(self.normalize(brands=["Tesco", "Tesco Finest"]).brand, "Tesco")

    def test_brands_missing_or_empty(self):
        self.assertEqual(self.normalize().brand, "")
        self.assertEqual(self.normalize(brands=[]).brand, "")
        self.assertEqual(self.normalize(brands=None).brand, "")

    def test_a_name_keeps_its_commas(self):
        entry = self.normalize(product_name="Beef, minced, 20% fat")
        self.assertEqual(entry.name, "Beef, minced, 20% fat")

    def test_a_name_arriving_as_a_list(self):
        self.assertEqual(self.normalize(product_name=["Ground beef"]).name, "Ground beef")

    def test_a_language_keyed_name(self):
        entry = self.normalize(product_name={"fr": "Boeuf haché", "en": "Ground beef"})
        self.assertEqual(entry.name, "Ground beef")

    def test_a_nameless_product_still_normalises(self):
        entry = self.normalize(product_name=None, product_name_en=None)
        self.assertEqual(entry.name, "Unnamed product")

    def test_a_serving_size_arriving_as_a_list(self):
        self.assertEqual(self.normalize(serving_size=["100 g"]).serving_unit, "100 g")

    def test_nutriments_of_the_wrong_shape_do_not_crash(self):
        entry = self.normalize(nutriments=[])
        self.assertIsNone(entry.energy_kcal)

    def test_a_whole_search_a_licious_shaped_hit(self):
        hit = {
            "code": "3017620422003",
            "product_name": ["Ground beef 20% fat"],
            "brands": ["Tesco"],
            "serving_size": ["125 g"],
            "nutriments": {"energy-kcal_100g": 250, "proteins_100g": 17},
        }
        with patch.object(self.module, "_post", return_value={"hits": [hit]}):
            found = self.module.search_by_name("ground beef")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].brand, "Tesco")
        self.assertEqual(found[0].name, "Ground beef 20% fat")
