"""What a recipe adds up to, from its ingredient lines."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from nutrition.models import CoreMacro, Food, Macro, MealFood, Micro, MicroCategory, Unit
from recipes import references
from recipes.breakdown import food_for_line, recipe_breakdown
from recipes.models import Ingredient, Recipe, RecipeIngredient, Reference, UserFoodReference


class BreakdownTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_nutrition", verbosity=0)
        cls.cook = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        cls.gram = Unit.objects.get(symbol="g")
        cls.kcal = Unit.objects.get(symbol="kcal")
        cls.mg = Unit.objects.get(symbol="mg")

    def setUp(self):
        references.forget()

    def food(self, name, energy=None, protein=None, iron=None, **kwargs):
        food = Food.objects.create(name=name, reference_unit=self.gram, **kwargs)
        if energy is not None:
            Macro.objects.create(
                food=food, core_macro=CoreMacro.objects.get(slug="energy"),
                component_name="Energy", unit=self.kcal, unit_count=Decimal(energy),
            )
        if protein is not None:
            Macro.objects.create(
                food=food, core_macro=CoreMacro.objects.get(slug="protein"),
                component_name="Protein", unit=self.gram, unit_count=Decimal(protein),
            )
        if iron is not None:
            Micro.objects.create(
                food=food, category=MicroCategory.objects.get(slug="minerals"),
                micro_name="Iron", unit=self.mg, unit_count=Decimal(iron),
            )
        return food

    def recipe(self, servings=4, lines=()):
        recipe = Recipe.objects.create(
            title="Dal", author=self.cook, servings=servings, is_shared=True
        )
        for name, quantity, unit in lines:
            RecipeIngredient.objects.create(
                recipe=recipe,
                ingredient=Ingredient.from_name(name),
                quantity=Decimal(quantity) if quantity else None,
                unit=unit,
            )
        return recipe

    # --- totals -------------------------------------------------------------

    def test_a_line_is_counted_at_the_amount_the_recipe_calls_for(self):
        self.food("Red lentils", energy=352, protein="24.6")
        recipe = self.recipe(lines=[("Red lentils", "250", "g")])
        report = recipe_breakdown(recipe, self.cook)
        self.assertTrue(report["complete"])
        self.assertEqual(report["energy_whole"], Decimal("880.0"))

    def test_per_serving_divides_by_the_recipe_servings(self):
        self.food("Red lentils", energy=352)
        recipe = self.recipe(servings=4, lines=[("Red lentils", "250", "g")])
        report = recipe_breakdown(recipe, self.cook)
        self.assertEqual(report["energy_each"], Decimal("220.0"))

    def test_several_lines_add_up(self):
        self.food("Red lentils", energy=352)
        self.food("Olive oil", energy=884)
        recipe = self.recipe(
            lines=[("Red lentils", "250", "g"), ("Olive oil", "30", "g")]
        )
        report = recipe_breakdown(recipe, self.cook)
        self.assertEqual(report["energy_whole"], Decimal("1145.2"))
        self.assertEqual(report["counted"], 2)

    def test_macros_and_micros_come_back_grouped(self):
        self.food("Red lentils", energy=352, protein="24.6", iron="7.5")
        recipe = self.recipe(lines=[("Red lentils", "100", "g")])
        groups = {g["label"]: g for g in recipe_breakdown(recipe, self.cook)["groups"]}
        self.assertEqual(sorted(groups), ["Macronutrients", "Minerals"])
        self.assertEqual(
            [row["name"] for row in groups["Macronutrients"]["rows"]], ["Protein"]
        )
        self.assertEqual([row["name"] for row in groups["Minerals"]["rows"]], ["Iron"])

    def test_energy_is_not_repeated_inside_the_macros(self):
        """It is drawn on its own; listing it twice reads as two measurements."""
        self.food("Red lentils", energy=352, protein="24.6")
        recipe = self.recipe(lines=[("Red lentils", "100", "g")])
        groups = recipe_breakdown(recipe, self.cook)["groups"]
        names = [row["name"] for group in groups for row in group["rows"]]
        self.assertNotIn("Energy", names)

    def test_each_row_carries_both_figures(self):
        self.food("Red lentils", protein="24.6")
        recipe = self.recipe(servings=2, lines=[("Red lentils", "200", "g")])
        row = recipe_breakdown(recipe, self.cook)["groups"][0]["rows"][0]
        self.assertEqual(row["whole"], Decimal("49.200"))
        self.assertEqual(row["each"], Decimal("24.600"))

    def test_micronutrient_units_survive(self):
        self.food("Red lentils", iron="7.5")
        recipe = self.recipe(lines=[("Red lentils", "100", "g")])
        row = recipe_breakdown(recipe, self.cook)["groups"][0]["rows"][0]
        # Totals are held in base units — grams — however the food recorded it.
        self.assertEqual(row["unit"], "g")
        self.assertEqual(row["whole"], Decimal("0.0075"))

    # --- how a line finds its food -----------------------------------------

    def test_a_name_match_is_used_when_nobody_has_chosen(self):
        self.food("Red lentils", energy=352)
        recipe = self.recipe(lines=[("red lentils", "100", "g")])
        line = recipe.recipe_ingredients.get()
        food, how = food_for_line(line, self.cook)
        self.assertEqual(how, "matched by name")
        self.assertEqual(food.name, "Red lentils")

    def test_a_bound_reference_beats_a_name_match(self):
        """An explicit choice outranks a lookup."""
        named = self.food("Red lentils", energy=352)
        chosen = self.food("Red lentils, organic", energy=300)

        recipe = self.recipe(lines=[("red lentils", "100", "g")])
        reference = Reference.objects.create(label="Red lentils")
        reference.patterns.create(pattern="red lentils")
        references.forget()
        UserFoodReference.objects.create(
            user=self.cook, recipe=recipe, reference=reference,
            meal_food=MealFood.objects.create(source_food=chosen),
        )

        line = recipe.recipe_ingredients.get()
        food, how = food_for_line(line, self.cook, bindings=references.bindings_for(self.cook, recipe))
        self.assertEqual(food, chosen)
        self.assertEqual(how, "you chose it")
        self.assertNotEqual(food, named)

    def test_nothing_fuzzier_than_an_exact_name(self):
        """Matching 'beef' to 'Beef dripping' would be inventing an answer."""
        self.food("Beef dripping", energy=898)
        recipe = self.recipe(lines=[("beef", "100", "g")])
        food, how = food_for_line(recipe.recipe_ingredients.get(), self.cook)
        self.assertIsNone(food)

    # --- gaps ---------------------------------------------------------------

    def test_an_unmatched_line_is_named_not_dropped(self):
        self.food("Red lentils", energy=352)
        recipe = self.recipe(
            lines=[("Red lentils", "250", "g"), ("Galangal", "10", "g")]
        )
        report = recipe_breakdown(recipe, self.cook)
        self.assertFalse(report["complete"])
        self.assertEqual(report["counted"], 1)
        self.assertEqual(report["gaps"][0]["line"].ingredient.name, "Galangal")
        self.assertIn("no food chosen", report["gaps"][0]["reason"])

    def test_a_line_that_cannot_be_converted_says_why(self):
        self.food("Olive oil", energy=884)
        recipe = self.recipe(lines=[("Olive oil", "2", "tbsp")])
        report = recipe_breakdown(recipe, self.cook)
        self.assertIn("density", report["gaps"][0]["reason"])

    def test_a_density_closes_that_gap(self):
        self.food("Olive oil", energy=884, density_g_per_ml=Decimal("0.92"))
        recipe = self.recipe(lines=[("Olive oil", "2", "tbsp")])
        report = recipe_breakdown(recipe, self.cook)
        self.assertTrue(report["complete"])
        self.assertAlmostEqual(float(report["energy_whole"]), 240.5, places=0)

    def test_a_recipe_with_no_ingredients_is_not_complete(self):
        self.assertFalse(recipe_breakdown(self.recipe(), self.cook)["complete"])

    def test_a_line_with_no_amount_is_a_gap(self):
        self.food("Salt", energy=0)
        recipe = self.recipe(lines=[("Salt", None, "")])
        self.assertTrue(recipe_breakdown(recipe, self.cook)["gaps"])

    # --- on the page --------------------------------------------------------

    def test_the_dropdown_is_on_the_recipe_page(self):
        self.food("Red lentils", energy=352)
        recipe = self.recipe(lines=[("Red lentils", "250", "g")])
        response = self.client.get(recipe.get_absolute_url())
        self.assertContains(response, "nutrition-panel")
        self.assertContains(response, "kcal a serving")

    def test_the_page_names_what_it_could_not_count(self):
        recipe = self.recipe(lines=[("Galangal", "10", "g")])
        response = self.client.get(recipe.get_absolute_url())
        self.assertContains(response, "Not counted")
        self.assertContains(response, "Galangal")

    def test_turning_the_servings_dial_does_not_change_a_serving(self):
        """The dial rescales what you buy, not what a portion contains."""
        self.food("Red lentils", energy=352)
        recipe = self.recipe(servings=4, lines=[("Red lentils", "250", "g")])
        plain = self.client.get(recipe.get_absolute_url()).context["breakdown"]
        scaled = self.client.get(
            recipe.get_absolute_url(), {"servings": "8"}
        ).context["breakdown"]
        self.assertEqual(plain["energy_each"], scaled["energy_each"])
