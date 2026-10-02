"""Tests for the Pantry.

Concentrated on the three places the design makes a deliberate choice and
would otherwise fail quietly: the catalogue/stock split, the append-only
ledger staying in step with each lot's balance, and resolution reporting its
gaps instead of guessing past them.
"""

from decimal import Decimal

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from pantry import services
from pantry.forms import FoodEntryForm
from pantry.modules import SourceError
from pantry.views import (
    DEFAULT_PAGE_SIZE,
    MAX_IMPORTS_PER_SUBMIT,
    PAGE_SIZES,
    _parse_selection,
)
from pantry.models import (
    FoodEntry,
    FoodPortion,
    IngredientDefault,
    MovementKind,
    PantryItem,
    SourceAPI,
    StockMovement,
    UserAPICredential,
)
from recipes.models import Ingredient, Recipe, RecipeIngredient


def food(**overrides):
    values = {
        "name": "Red lentils",
        "energy_kcal": Decimal("352"),
        "protein_g": Decimal("24.6"),
        "carbohydrate_g": Decimal("63.1"),
        "fat_g": Decimal("1.1"),
    }
    values.update(overrides)
    return FoodEntry.objects.create(**values)


class CatalogueTests(TestCase):
    def test_nutrients_scale_from_per_100g(self):
        entry = food()
        values = entry.nutrients_for(Decimal("250"))
        self.assertEqual(values["energy_kcal"], Decimal("880.000"))
        self.assertEqual(values["protein_g"], Decimal("61.500"))

    def test_an_unknown_nutrient_stays_unknown_rather_than_becoming_zero(self):
        entry = food(sodium_mg=None)
        self.assertIsNone(entry.nutrients_for(Decimal("100"))["sodium_mg"])

    def test_retired_foods_leave_the_pickers_but_stay_readable(self):
        entry = food(is_active=False)
        self.assertFalse(FoodEntry.objects.filter(pk=entry.pk).exists())
        self.assertTrue(FoodEntry.all_objects.filter(pk=entry.pk).exists())

    def test_one_row_per_food_per_source(self):
        food(source_api=SourceAPI.OFF, external_id="123")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                food(source_api=SourceAPI.OFF, external_id="123")

    def test_hand_entered_foods_are_not_constrained_by_external_id(self):
        food(name="Mum's chutney")
        food(name="Dad's chutney")
        self.assertEqual(FoodEntry.objects.count(), 2)

    def test_the_same_product_from_both_sources_fingerprints_alike(self):
        a = food(source_api=SourceAPI.FDC, external_id="1", barcode="500")
        b = food(source_api=SourceAPI.OFF, external_id="2", barcode="500")
        self.assertEqual(a.fingerprint, b.fingerprint)

    def test_has_macros_is_false_for_a_bare_entry(self):
        self.assertFalse(
            FoodEntry.objects.create(name="Mystery").has_macros
        )


class ResolutionTests(TestCase):
    """Recipes name things; the pantry knows foods. This is the join."""

    @classmethod
    def setUpTestData(cls):
        cls.cook = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        cls.lentils = Ingredient.from_name("red lentils")
        cls.entry = food()
        cls.recipe = Recipe.objects.create(title="Dal", author=cls.cook, servings=4)
        cls.line = RecipeIngredient.objects.create(
            recipe=cls.recipe, ingredient=cls.lentils, quantity=Decimal("250"), unit="g"
        )

    def test_without_a_default_the_recipe_line_is_unresolved(self):
        self.assertIsNone(services.entry_for(self.cook, self.lentils))
        grams, problem = services.line_grams(self.line, None)
        self.assertIsNone(grams)
        self.assertIn("no food chosen", problem)

    def test_a_default_resolves_it(self):
        IngredientDefault.objects.create(
            user=self.cook, ingredient=self.lentils, entry=self.entry
        )
        self.assertEqual(services.entry_for(self.cook, self.lentils), self.entry)
        grams, problem = services.line_grams(self.line, self.entry)
        self.assertEqual(grams, Decimal("250"))
        self.assertIsNone(problem)

    def test_defaults_are_per_person(self):
        other = get_user_model().objects.create_user("other", password="hunter2hunter2")
        IngredientDefault.objects.create(
            user=self.cook, ingredient=self.lentils, entry=self.entry
        )
        self.assertIsNone(services.entry_for(other, self.lentils))

    def test_one_default_per_person_per_term(self):
        IngredientDefault.objects.create(
            user=self.cook, ingredient=self.lentils, entry=self.entry
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                IngredientDefault.objects.create(
                    user=self.cook, ingredient=self.lentils, entry=food(name="Other lentils")
                )

    def test_a_count_needs_a_portion_weight_and_says_so(self):
        eggs = Ingredient.from_name("eggs")
        entry = food(name="Egg", energy_kcal=Decimal("143"))
        line = RecipeIngredient.objects.create(
            recipe=self.recipe, ingredient=eggs, quantity=Decimal("2"), unit=""
        )
        grams, problem = services.line_grams(line, entry)
        self.assertIsNone(grams)
        self.assertTrue(problem)

        FoodPortion.objects.create(entry=entry, unit="", grams=Decimal("58"))
        entry.refresh_from_db()
        grams, problem = services.line_grams(line, entry)
        self.assertEqual(grams, Decimal("116"))

    def test_nutrition_reports_the_lines_it_could_not_price(self):
        oil = Ingredient.from_name("olive oil")
        RecipeIngredient.objects.create(
            recipe=self.recipe, ingredient=oil, quantity=Decimal("2"), unit="tbsp"
        )
        IngredientDefault.objects.create(
            user=self.cook, ingredient=self.lentils, entry=self.entry
        )
        report = services.recipe_nutrition(self.recipe, self.cook)
        self.assertFalse(report["complete"])
        self.assertEqual([p["ingredient"] for p in report["problems"]], ["olive oil"])
        self.assertGreater(report["totals"]["energy_kcal"], 0)


class StockTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cook = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        cls.entry = food()

    def lot(self, grams, expires=None):
        item = PantryItem.objects.create(
            owner=self.cook, entry=self.entry, expires_on=expires
        )
        services.record(item, MovementKind.ADD, Decimal(grams))
        return item

    def test_the_balance_follows_the_ledger(self):
        item = self.lot(500)
        services.record(item, MovementKind.CONSUME, Decimal("-120"))
        item.refresh_from_db()
        self.assertEqual(item.quantity_g, Decimal("380"))
        self.assertEqual(StockMovement.balance_for(item), Decimal("380"))

    def test_stock_of_sums_every_lot(self):
        self.lot(500)
        self.lot(250)
        self.assertEqual(services.stock_of(self.cook, self.entry), Decimal("750"))

    def test_the_soonest_to_expire_is_opened_first(self):
        today = timezone.localdate()
        later = self.lot(500, expires=today + timezone.timedelta(days=30))
        sooner = self.lot(500, expires=today + timezone.timedelta(days=2))
        services.draw(self.cook, self.entry, Decimal("300"))
        sooner.refresh_from_db()
        later.refresh_from_db()
        self.assertEqual(sooner.quantity_g, Decimal("200"))
        self.assertEqual(later.quantity_g, Decimal("500"))

    def test_an_undated_lot_waits_behind_a_dated_one(self):
        today = timezone.localdate()
        undated = self.lot(500)
        dated = self.lot(500, expires=today + timezone.timedelta(days=5))
        services.draw(self.cook, self.entry, Decimal("100"))
        dated.refresh_from_db()
        undated.refresh_from_db()
        self.assertEqual(dated.quantity_g, Decimal("400"))
        self.assertEqual(undated.quantity_g, Decimal("500"))

    def test_running_short_is_reported_not_raised(self):
        self.lot(100)
        drawn, short = services.draw(self.cook, self.entry, Decimal("250"))
        self.assertEqual(drawn, Decimal("100"))
        self.assertEqual(short, Decimal("150"))

    def test_drawing_spans_lots(self):
        self.lot(100)
        self.lot(100)
        drawn, short = services.draw(self.cook, self.entry, Decimal("150"))
        self.assertEqual(drawn, Decimal("150"))
        self.assertEqual(short, Decimal("0"))

    def test_stock_is_one_persons(self):
        other = get_user_model().objects.create_user("other", password="hunter2hunter2")
        self.lot(500)
        self.assertEqual(services.stock_of(other, self.entry), Decimal("0"))

    def test_cooking_draws_stock_and_is_not_intake(self):
        self.lot(500)
        IngredientDefault.objects.create(
            user=self.cook, ingredient=Ingredient.from_name("red lentils"), entry=self.entry
        )
        recipe = Recipe.objects.create(title="Dal", author=self.cook, servings=4)
        RecipeIngredient.objects.create(
            recipe=recipe,
            ingredient=Ingredient.objects.get(name="red lentils"),
            quantity=Decimal("250"),
            unit="g",
        )
        report = services.cook_from_recipe(recipe, self.cook)
        self.assertEqual(report["shortfalls"], [])
        self.assertEqual(services.stock_of(self.cook, self.entry), Decimal("250"))
        self.assertFalse(
            StockMovement.objects.filter(kind=MovementKind.EATEN).exists()
        )

    def test_cooking_what_you_are_short_of_still_records_what_left(self):
        self.lot(100)
        IngredientDefault.objects.create(
            user=self.cook, ingredient=Ingredient.from_name("red lentils"), entry=self.entry
        )
        recipe = Recipe.objects.create(title="Dal", author=self.cook, servings=4)
        RecipeIngredient.objects.create(
            recipe=recipe,
            ingredient=Ingredient.objects.get(name="red lentils"),
            quantity=Decimal("250"),
            unit="g",
        )
        report = services.cook_from_recipe(recipe, self.cook)
        self.assertEqual(services.stock_of(self.cook, self.entry), Decimal("0"))
        self.assertEqual(len(report["shortfalls"]), 1)
        self.assertEqual(report["shortfalls"][0]["short_g"], Decimal("150"))


class AvailabilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cook = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        cls.entry = food()
        cls.lentils = Ingredient.from_name("red lentils")
        cls.recipe = Recipe.objects.create(title="Dal", author=cls.cook, servings=4)
        RecipeIngredient.objects.create(
            recipe=cls.recipe, ingredient=cls.lentils, quantity=Decimal("250"), unit="g"
        )

    def test_cannot_tell_and_havent_got_are_different_answers(self):
        report = services.availability(self.recipe, self.cook)
        self.assertEqual(len(report["unknown"]), 1)
        self.assertEqual(report["missing"], [])
        self.assertFalse(report["cookable"])

    def test_enough_in_stock_is_cookable(self):
        IngredientDefault.objects.create(
            user=self.cook, ingredient=self.lentils, entry=self.entry
        )
        item = PantryItem.objects.create(owner=self.cook, entry=self.entry)
        services.record(item, MovementKind.ADD, Decimal("500"))
        report = services.availability(self.recipe, self.cook)
        self.assertTrue(report["cookable"])
        self.assertEqual(report["ratio"], 1.0)

    def test_scaling_up_can_make_it_uncookable(self):
        IngredientDefault.objects.create(
            user=self.cook, ingredient=self.lentils, entry=self.entry
        )
        item = PantryItem.objects.create(owner=self.cook, entry=self.entry)
        services.record(item, MovementKind.ADD, Decimal("300"))
        self.assertTrue(services.availability(self.recipe, self.cook)["cookable"])
        self.assertFalse(
            services.availability(self.recipe, self.cook, servings=8)["cookable"]
        )


class CredentialTests(TestCase):
    def test_the_key_is_ciphertext_in_the_column_and_plaintext_in_python(self):
        user = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        UserAPICredential.objects.create(user=user, api=SourceAPI.FDC, key="SECRET-KEY-123")

        stored = UserAPICredential.objects.get()
        self.assertEqual(stored.key, "SECRET-KEY-123")

        with self.assertNumQueries(1):
            raw = UserAPICredential.objects.values_list("key", flat=True)[0]
        self.assertNotIn("SECRET-KEY-123", raw)

    def test_having_no_key_is_a_normal_state(self):
        user = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        self.assertIsNone(services.api_key_for(user, SourceAPI.FDC))


class AccessTests(TestCase):
    def test_the_pantry_needs_an_account(self):
        for name in ("pantry:home", "pantry:cook", "pantry:search", "pantry:credentials"):
            with self.subTest(view=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response["Location"])

    def test_signed_in_readers_get_the_pantry(self):
        get_user_model().objects.create_user("cook", password="hunter2hunter2")
        self.client.login(username="cook", password="hunter2hunter2")
        self.assertEqual(self.client.get(reverse("pantry:home")).status_code, 200)

    def test_the_encryption_key_check_warns_while_the_fallback_is_in_use(self):
        from pantry.checks import check_encryption_key

        with self.settings(PANTRY_ENCRYPTION_KEY=""):
            self.assertEqual([w.id for w in check_encryption_key(None)], ["pantry.W001"])
        with self.settings(PANTRY_ENCRYPTION_KEY="a-key-of-its-own"):
            self.assertEqual(check_encryption_key(None), [])


class SearchTableTests(TestCase):
    """The results table: seven columns, in order, for both result sets."""

    COLUMNS = ["Source", "Company", "Product", "Calories", "Protein",
               "Carbohydrates", "Fats"]

    @classmethod
    def setUpTestData(cls):
        cls.cook = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        cls.entry = food(
            name="Red lentils", brand="Suma", source_api=SourceAPI.OFF, external_id="9"
        )

    def setUp(self):
        self.client.force_login(self.cook)

    def page(self, **params):
        return self.client.get(reverse("pantry:search"), params)

    def test_the_headings_are_in_the_order_asked_for(self):
        response = self.page(q="lentils")
        body = response.content.decode()
        positions = [body.index(f">{column}<") for column in self.COLUMNS]
        self.assertEqual(positions, sorted(positions))

    def test_each_entry_becomes_a_row_of_its_numbers(self):
        response = self.page(q="lentils")
        body = response.content.decode()
        for cell in ("Suma", "Red lentils", "352", "24.6", "63.1", "1.1"):
            with self.subTest(cell=cell):
                self.assertIn(cell, body)

    def test_the_source_is_shown_per_row(self):
        self.assertContains(self.page(q="lentils"), SourceAPI.OFF)

    def test_a_missing_figure_is_a_dash_rather_than_a_blank(self):
        FoodEntry.objects.create(name="Mystery powder", protein_g=None)
        self.assertContains(self.page(q="Mystery"), "—")

    def test_a_zero_is_not_mistaken_for_missing(self):
        """`{% if value %}` would print a dash for a genuine zero."""
        FoodEntry.objects.create(name="Spring water", energy_kcal=Decimal("0"),
                                 protein_g=Decimal("0"), carbohydrate_g=Decimal("0"),
                                 fat_g=Decimal("0"))
        body = self.page(q="Spring water").content.decode()
        row = body[body.index("Spring water"):][:600]
        self.assertIn("0.0", row)
        self.assertIn(">0<", row.replace(" ", "").replace("\n", ""))

    def test_the_table_scrolls_rather_than_the_page(self):
        self.assertContains(self.page(q="lentils"), "table-scroll")

    def test_no_table_when_nothing_matches(self):
        self.assertNotContains(self.page(q="zzzzzz"), "<table")


class CompanySearchTests(TestCase):
    """Brands are free text off two APIs, so the same company arrives spelled
    several ways. Searching has to see through that."""

    @classmethod
    def setUpTestData(cls):
        cls.cook = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        for name, brand in [
            ("Rolled oats", "Flahavan's"),
            ("Steel cut oats", "flahavan's"),
            ("Porridge oats", "FLAHAVAN'S"),
            ("Baked beans", "Heinz"),
            ("Plain flour", ""),
        ]:
            FoodEntry.objects.create(name=name, brand=brand)

    def test_a_company_is_found_whatever_the_case(self):
        for typed in ("flahavan", "FLAHAVAN", "Flahavan", "hAvAn"):
            with self.subTest(typed=typed):
                found = services.search_companies(typed)
                self.assertEqual([row["name"] for row in found], ["Flahavan's"])

    def test_spellings_fold_into_one_company(self):
        found = services.search_companies("flahavan")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["count"], 3)

    def test_the_label_is_the_commonest_spelling_not_the_first_seen(self):
        FoodEntry.objects.create(name="Oatmeal", brand="flahavan's")
        # Now two lowercase against one of each other casing.
        self.assertEqual(services.search_companies("flahavan")[0]["name"], "flahavan's")

    def test_companies_are_ranked_by_how_much_they_stock(self):
        found = services.search_companies("a")
        names = [row["name"] for row in found]
        self.assertEqual(names[0], "Flahavan's")
        self.assertIn("Heinz", names)

    def test_unbranded_foods_are_not_a_company(self):
        self.assertEqual(services.search_companies(""), [])
        for row in services.search_companies("a"):
            self.assertTrue(row["name"])

    def test_nothing_matching_is_an_empty_list(self):
        self.assertEqual(services.search_companies("zzzz"), [])

    def test_filtering_the_catalogue_by_company_ignores_case(self):
        found = services.search_local(company="FLAHAVAN")
        self.assertEqual(len(found), 3)

    def test_company_and_product_narrow_together(self):
        found = services.search_local(term="rolled", company="flahavan")
        self.assertEqual([entry.name for entry in found], ["Rolled oats"])

    def test_a_barcode_still_wins_outright(self):
        FoodEntry.objects.create(name="Scanned thing", barcode="500", brand="Heinz")
        found = services.search_local(barcode="500", company="flahavan")
        self.assertEqual([entry.name for entry in found], ["Scanned thing"])

    def test_the_search_page_offers_a_company_box(self):
        self.client.force_login(self.cook)
        response = self.client.get(reverse("pantry:search"))
        self.assertContains(response, 'name="company"')

    def test_the_page_lists_matching_companies_to_pick_from(self):
        self.client.force_login(self.cook)
        response = self.client.get(reverse("pantry:search"), {"company": "flahavan"})
        self.assertContains(response, "Flahavan&#x27;s")
        self.assertEqual(len(response.context["local"]), 3)

    def test_typing_a_company_into_the_product_box_still_surfaces_it(self):
        self.client.force_login(self.cook)
        response = self.client.get(reverse("pantry:search"), {"q": "heinz"})
        self.assertEqual([row["name"] for row in response.context["companies"]], ["Heinz"])


class BatchImportTests(TestCase):
    """Importing one food, several, or a selection that partly fails."""

    @classmethod
    def setUpTestData(cls):
        cls.cook = get_user_model().objects.create_user("cook", password="hunter2hunter2")

    def setUp(self):
        self.client.force_login(self.cook)
        self.url = reverse("pantry:import")

    def fake_import(self, user, api, external_id):
        return FoodEntry.objects.create(
            name=f"Food {external_id}", source_api=api, external_id=external_id
        )

    def post(self, data, importer=None):
        with patch.object(services, "import_food", side_effect=importer or self.fake_import):
            return self.client.post(self.url, data)

    def test_a_single_row_button_imports_one(self):
        response = self.post({"only": "OFF:111"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual([e.external_id for e in FoodEntry.objects.all()], ["111"])

    def test_ticked_boxes_import_together(self):
        self.post({"selected": ["OFF:111", "FDC:222", "OFF:333"]})
        self.assertEqual(FoodEntry.objects.count(), 3)

    def test_a_row_button_beats_the_ticked_boxes(self):
        """Ticking three then pressing one row's Import imports that one."""
        self.post({"only": "OFF:999", "selected": ["OFF:111", "FDC:222"]})
        self.assertEqual([e.external_id for e in FoodEntry.objects.all()], ["999"])

    def test_one_failure_does_not_lose_the_rest(self):
        def flaky(user, api, external_id):
            if external_id == "222":
                raise SourceError(api, "gateway fell over")
            return self.fake_import(user, api, external_id)

        response = self.post({"selected": ["OFF:111", "FDC:222", "OFF:333"]}, importer=flaky)
        self.assertEqual(FoodEntry.objects.count(), 2)
        text = " ".join(str(m) for m in get_messages(response.wsgi_request))
        self.assertIn("Imported 2 foods", text)
        self.assertIn("gateway fell over", text)

    def test_an_unknown_source_is_rejected_not_attempted(self):
        response = self.post({"selected": ["EVIL:111", "OFF:222"]})
        self.assertEqual(FoodEntry.objects.count(), 1)
        text = " ".join(str(m) for m in get_messages(response.wsgi_request))
        self.assertIn("not recognised", text)

    def test_a_malformed_selection_is_rejected(self):
        for token in ("", ":", "OFF:", "nonsense"):
            with self.subTest(token=token):
                self.assertIsNone(_parse_selection(token))
        self.assertEqual(_parse_selection("OFF:111"), ("OFF", "111"))

    def test_importing_nothing_says_so_rather_than_succeeding_quietly(self):
        response = self.client.post(self.url, {})
        text = " ".join(str(m) for m in get_messages(response.wsgi_request))
        self.assertIn("Nothing was selected", text)

    def test_a_huge_selection_is_capped(self):
        tokens = [f"OFF:{n}" for n in range(MAX_IMPORTS_PER_SUBMIT + 5)]
        response = self.post({"selected": tokens})
        self.assertEqual(FoodEntry.objects.count(), MAX_IMPORTS_PER_SUBMIT)
        text = " ".join(str(m) for m in get_messages(response.wsgi_request))
        self.assertIn("select fewer", text)

    def test_it_returns_to_the_search_that_produced_the_results(self):
        response = self.post(
            {"only": "OFF:111", "q": "oats", "company": "Flahavan", "external": "1"}
        )
        self.assertIn("q=oats", response["Location"])
        self.assertIn("company=Flahavan", response["Location"])
        self.assertIn(reverse("pantry:search"), response["Location"])

    def test_importing_needs_an_account_and_a_post(self):
        self.client.logout()
        self.assertEqual(self.client.post(self.url, {"only": "OFF:1"}).status_code, 302)
        self.client.force_login(self.cook)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_the_results_table_offers_a_tick_per_row_and_a_select_all(self):
        from pantry.modules.base import NormalizedFood

        result = NormalizedFood(
            source_api=SourceAPI.OFF,
            external_id="77",
            name="Rolled oats",
            brand="Flahavan's",
            energy_kcal=Decimal("372"),
        )
        with patch.object(services, "search_external", return_value=([result], [])):
            response = self.client.get(
                reverse("pantry:search"), {"q": "oats", "external": "1"}
            )

        self.assertContains(response, 'name="selected" value="OFF:77"')
        self.assertContains(response, "data-select-all")
        self.assertContains(response, 'name="only" value="OFF:77"')
        self.assertContains(response, "Import selected")

    def test_the_tables_are_marked_up_as_resizable(self):
        food(name="Oats")
        response = self.client.get(reverse("pantry:search"), {"q": "oats"})
        self.assertContains(response, "data-resizable")
        self.assertContains(response, 'data-table-key="pantry-local"')


class PaginationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cook = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        for number in range(30):
            FoodEntry.objects.create(name=f"Oat product {number:02d}", brand="Flahavan's")

    def setUp(self):
        self.client.force_login(self.cook)

    def page(self, **params):
        params.setdefault("q", "Oat product")
        return self.client.get(reverse("pantry:search"), params)

    def test_results_come_a_page_at_a_time(self):
        response = self.page()
        self.assertEqual(len(response.context["local"]), DEFAULT_PAGE_SIZE)
        self.assertEqual(response.context["local_page"].paginator.count, 30)

    def test_the_page_size_can_be_changed(self):
        self.assertEqual(len(self.page(per_page=10).context["local"]), 10)
        self.assertEqual(len(self.page(per_page=50).context["local"]), 30)

    def test_an_unoffered_page_size_falls_back(self):
        for silly in ("7", "0", "-5", "nonsense", "999999"):
            with self.subTest(size=silly):
                self.assertEqual(self.page(per_page=silly).context["per_page"],
                                 DEFAULT_PAGE_SIZE)

    def test_turning_the_page(self):
        first = self.page(per_page=10, page=1).context["local"]
        second = self.page(per_page=10, page=2).context["local"]
        self.assertNotEqual([e.pk for e in first], [e.pk for e in second])
        self.assertEqual(second[0].name, "Oat product 10")

    def test_a_page_past_the_end_shows_the_last_one_rather_than_404ing(self):
        response = self.page(per_page=10, page=99)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["local_page"].number, 3)

    def test_a_nonsense_page_number_is_page_one(self):
        self.assertEqual(self.page(page="banana").context["local_page"].number, 1)

    def test_the_two_lists_page_independently(self):
        """Turning the open-source page must not reset the catalogue page."""
        response = self.page(per_page=10, page=3, xpage=1)
        self.assertEqual(response.context["local_page"].number, 3)
        self.assertEqual(response.context["cluster_page"].number, 1)

    def test_the_size_dropdown_offers_every_choice(self):
        response = self.page()
        for size in PAGE_SIZES:
            self.assertContains(response, f'value="{size}"')

    def test_the_pager_carries_the_filters(self):
        response = self.page(per_page=10, company="Flahavan")
        body = response.content.decode()
        self.assertIn("company=Flahavan", body)
        self.assertIn("per_page=10", body)

    def test_one_page_of_results_needs_no_pager(self):
        self.assertNotContains(self.page(per_page=50), "rel=\"next\"")


class NutrientFieldTests(TestCase):
    """The nutrient list is duplicated across the model, the normaliser and
    the source maps. Duplication is fine; drift is not, so it is asserted."""

    def test_the_model_and_the_normaliser_agree(self):
        """`import_food` reads every FoodEntry.NUTRIENTS name off a
        NormalizedFood, so a field on one and not the other is an
        AttributeError on the next import."""
        from pantry.modules.base import NormalizedFood

        self.assertEqual(set(FoodEntry.NUTRIENTS), set(NormalizedFood.NUTRIENTS))
        blank = NormalizedFood(source_api="MANUAL", external_id="x", name="x")
        for field in FoodEntry.NUTRIENTS:
            with self.subTest(field=field):
                self.assertIsNone(getattr(blank, field))

    def test_the_groups_cover_every_nutrient_exactly_once(self):
        seen = [field for _, fields in FoodEntry.NUTRIENT_GROUPS for field in fields]
        self.assertEqual(sorted(seen), sorted(FoodEntry.NUTRIENTS))
        self.assertEqual(len(seen), len(set(seen)))

    def test_macros_minerals_and_vitamins_are_all_present(self):
        self.assertIn("protein_g", FoodEntry.MACRONUTRIENTS)
        self.assertIn("iron_mg", FoodEntry.MINERALS)
        self.assertIn("vitamin_b12_ug", FoodEntry.VITAMINS)

    def test_no_fdc_id_maps_to_two_nutrients(self):
        from pantry.modules.fdc import NUTRIENT_IDS

        targets = list(NUTRIENT_IDS.values())
        self.assertEqual(len(targets), len(set(targets)), "an id was reused")
        for target in targets:
            self.assertIn(target, FoodEntry.NUTRIENTS)

    def test_off_mineral_scaling_targets_real_fields(self):
        from pantry.modules.off import OFF_DIRECT, OFF_SCALED

        for field, multiplier in OFF_SCALED.values():
            self.assertIn(field, FoodEntry.NUTRIENTS)
            self.assertTrue(field.endswith("_mg"), f"{field} is scaled to mg")
            self.assertEqual(multiplier, 1000)
        for field in OFF_DIRECT.values():
            self.assertIn(field, FoodEntry.NUTRIENTS)
            self.assertTrue(field.endswith("_g"))

    def test_off_scales_grams_into_milligrams(self):
        from pantry.modules.off import OpenFoodFactsModule

        entry = OpenFoodFactsModule().normalize(
            {
                "code": "1",
                "product_name": "Fortified oats",
                "nutriments": {"iron_100g": 0.0042, "calcium_100g": 0.35},
            }
        )
        self.assertEqual(entry.iron_mg, Decimal("4.200"))
        self.assertEqual(entry.calcium_mg, Decimal("350.000"))

    def test_an_absent_mineral_stays_absent_rather_than_becoming_zero(self):
        from pantry.modules.off import OpenFoodFactsModule

        entry = OpenFoodFactsModule().normalize(
            {"code": "1", "product_name": "Oats", "nutriments": {}}
        )
        self.assertIsNone(entry.iron_mg)
        self.assertIsNone(entry.vitamin_c_mg)

    def test_scaling_reaches_the_new_nutrients(self):
        entry = FoodEntry.objects.create(
            name="Fortified oats", iron_mg=Decimal("4.2"), vitamin_b12_ug=Decimal("1.5")
        )
        values = entry.nutrients_for(Decimal("50"))
        self.assertEqual(values["iron_mg"], Decimal("2.100"))
        self.assertEqual(values["vitamin_b12_ug"], Decimal("0.750"))

    def test_the_table_groups_and_keeps_blanks_out_of_recorded(self):
        entry = FoodEntry.objects.create(
            name="Oats", energy_kcal=Decimal("372"), iron_mg=Decimal("4.2")
        )
        table = {group["label"]: group for group in entry.nutrition_table()}
        self.assertEqual(list(table), ["Macronutrients", "Minerals", "Vitamins"])
        self.assertEqual([r["field"] for r in table["Minerals"]["recorded"]], ["iron_mg"])
        self.assertEqual(table["Vitamins"]["recorded"], [])
        self.assertTrue(table["Vitamins"]["rows"], "the heading still lists its fields")

    def test_units_come_off_the_field_name(self):
        from pantry.models import nutrient_unit

        self.assertEqual(nutrient_unit("energy_kcal"), "kcal")
        self.assertEqual(nutrient_unit("iron_mg"), "mg")
        self.assertEqual(nutrient_unit("protein_g"), "g")
        self.assertEqual(nutrient_unit("folate_ug"), "\u00b5g")


class EntryFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cook = get_user_model().objects.create_user("cook", password="hunter2hunter2")

    def setUp(self):
        self.client.force_login(self.cook)

    def test_the_form_offers_every_nutrient_and_a_photo(self):
        response = self.client.get(reverse("pantry:entry-create"))
        for field in FoodEntry.NUTRIENTS:
            with self.subTest(field=field):
                self.assertContains(response, f'name="{field}"')
        self.assertContains(response, 'name="image"')
        self.assertContains(response, 'enctype="multipart/form-data"')

    def test_the_groups_are_laid_out_separately(self):
        response = self.client.get(reverse("pantry:entry-create")).content.decode()
        for label in ("Macronutrients", "Minerals", "Vitamins"):
            self.assertIn(label, response)

    def test_saving_a_food_with_vitamins(self):
        response = self.client.post(
            reverse("pantry:entry-create"),
            {
                "name": "Fortified oats",
                "energy_kcal": "372",
                "iron_mg": "4.2",
                "vitamin_b12_ug": "1.5",
                "folate_ug": "110",
            },
        )
        self.assertEqual(response.status_code, 302)
        entry = FoodEntry.objects.get()
        self.assertEqual(entry.iron_mg, Decimal("4.200"))
        self.assertEqual(entry.folate_ug, Decimal("110.000"))
        self.assertIsNone(entry.zinc_mg)

    def test_every_nutrient_stays_optional(self):
        response = self.client.post(reverse("pantry:entry-create"), {"name": "Mystery"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(FoodEntry.objects.get().has_macros)

    def test_a_food_can_be_edited_to_fill_the_rest_in_later(self):
        entry = FoodEntry.objects.create(name="Oats", energy_kcal=Decimal("372"))
        self.client.post(
            reverse("pantry:entry-edit", args=[entry.pk]),
            {"name": "Oats", "energy_kcal": "372", "zinc_mg": "3.6"},
        )
        entry.refresh_from_db()
        self.assertEqual(entry.zinc_mg, Decimal("3.600"))

    def test_a_group_with_values_opens_on_the_edit_form(self):
        entry = FoodEntry.objects.create(name="Oats", zinc_mg=Decimal("3.6"))
        form = FoodEntryForm(instance=entry)
        groups = {group["label"]: group["filled"] for group in form.nutrient_groups()}
        self.assertTrue(groups["Minerals"])
        self.assertFalse(groups["Vitamins"])


class UnitPrecisionTests(TestCase):
    """OFF reports minerals in grams. Rounding before scaling loses them."""

    def normalize(self, nutriments):
        from pantry.modules.off import OpenFoodFactsModule

        return OpenFoodFactsModule().normalize(
            {"code": "1", "product_name": "Test", "nutriments": nutriments}
        )

    def test_a_trace_mineral_survives_the_conversion(self):
        """0.0042 g of iron is 4.2 mg, not 4.0 — rounding in grams first
        quantised it to the nearest milligram and lost the rest."""
        self.assertEqual(self.normalize({"iron_100g": 0.0042}).iron_mg, Decimal("4.200"))

    def test_a_microgram_quantity_does_not_round_to_nothing(self):
        self.assertEqual(self.normalize({"zinc_100g": 0.0000036}).zinc_mg, Decimal("0.004"))

    def test_sodium_keeps_its_precision_too(self):
        self.assertEqual(self.normalize({"sodium_100g": 0.0007}).sodium_mg, Decimal("0.700"))

    def test_salt_still_converts_to_sodium(self):
        # Sodium is 39.34% of salt by mass.
        self.assertEqual(self.normalize({"salt_100g": 1.2}).sodium_mg, Decimal("471.600"))

    def test_sodium_given_directly_beats_the_salt_estimate(self):
        entry = self.normalize({"sodium_100g": 0.5, "salt_100g": 99})
        self.assertEqual(entry.sodium_mg, Decimal("500.000"))
