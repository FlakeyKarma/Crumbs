"""Tests for the normalised nutrition schema.

Weighted towards the three things the shape exists to get right: unit
conversion through a base unit, a meal built out of other meals, and the
absence of a row meaning "unrecorded" rather than zero.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from nutrition import services
from nutrition.models import (
    CoreMacro,
    Dimension,
    Food,
    FoodTracking,
    Macro,
    Meal,
    MealFood,
    Micro,
    MicroCategory,
    Unit,
)


class Seeded(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_nutrition", verbosity=0)
        cls.g = Unit.objects.get(symbol="g")
        cls.mg = Unit.objects.get(symbol="mg")
        cls.ug = Unit.objects.get(symbol="\u00b5g")
        cls.kcal = Unit.objects.get(symbol="kcal")
        cls.kj = Unit.objects.get(symbol="kJ")

    def food(self, name="Oats", **kwargs):
        return Food.objects.create(name=name, reference_unit=self.g, **kwargs)

    def macro(self, food, slug, name, unit, count):
        return Macro.objects.create(
            food=food,
            core_macro=CoreMacro.objects.get(slug=slug),
            component_name=name,
            unit=unit,
            unit_count=Decimal(count),
        )

    def micro(self, food, slug, name, unit, count):
        return Micro.objects.create(
            food=food,
            category=MicroCategory.objects.get(slug=slug),
            micro_name=name,
            unit=unit,
            unit_count=Decimal(count),
        )


class UnitTests(Seeded):
    def test_the_base_unit_of_each_dimension_is_worth_one(self):
        for symbol in ("g", "ml", "kcal"):
            with self.subTest(symbol=symbol):
                self.assertTrue(Unit.objects.get(symbol=symbol).is_base)

    def test_conversion_runs_through_the_base_unit(self):
        self.assertEqual(self.mg.to_base_units(Decimal("4200")), Decimal("4.200"))
        self.assertEqual(self.ug.convert(Decimal("1000"), self.mg), Decimal("1"))
        self.assertEqual(self.g.convert(Decimal("2.5"), self.mg), Decimal("2500"))

    def test_a_microgram_does_not_round_away(self):
        """The reason unit_count is a decimal: 0.75 µg is not 1 and not 0."""
        self.assertEqual(self.ug.to_base_units(Decimal("0.75")), Decimal("0.00000075"))

    def test_kilojoules_convert_to_kilocalories(self):
        self.assertAlmostEqual(
            float(self.kj.convert(Decimal("1000"), self.kcal)), 239.005736, places=5
        )

    def test_mixing_dimensions_is_refused(self):
        with self.assertRaises(ValidationError):
            self.g.convert(Decimal("1"), self.kcal)

    def test_every_seeded_unit_knows_what_it_measures(self):
        for unit in Unit.objects.all():
            with self.subTest(unit=unit.symbol):
                self.assertIn(unit.dimension, Dimension.values)
                self.assertGreater(unit.to_base, 0)

    def test_seeding_twice_changes_nothing(self):
        before = Unit.objects.count()
        call_command("seed_nutrition", verbosity=0)
        self.assertEqual(Unit.objects.count(), before)


class FoodTests(Seeded):
    def test_a_food_carries_many_macros_and_micros(self):
        """The correction that made the schema workable: the key is on the
        row, so one food holds as many nutrients as it needs."""
        oats = self.food()
        self.macro(oats, "energy", "Energy", self.kcal, "372")
        self.macro(oats, "protein", "Protein", self.g, "11.2")
        self.macro(oats, "fat", "Total fat", self.g, "7.1")
        self.micro(oats, "minerals", "Iron", self.mg, "4.2")
        self.micro(oats, "vitamins", "Folate", self.ug, "110")

        self.assertEqual(oats.macros.count(), 3)
        self.assertEqual(oats.micros.count(), 2)

    def test_an_unrecorded_nutrient_is_an_absent_row(self):
        """Not a zero. The distinction the old wide table could not make."""
        oats = self.food()
        self.macro(oats, "energy", "Energy", self.kcal, "372")
        totals, _ = services.totals_for_food(oats)
        self.assertEqual(len(totals), 1)
        self.assertNotIn(("micro", "Minerals", "Iron", Dimension.MASS), totals)

    def test_a_nutrient_cannot_be_listed_twice_for_one_food(self):
        oats = self.food()
        self.macro(oats, "fat", "Saturated fat", self.g, "1.2")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.macro(oats, "fat", "Saturated fat", self.g, "9.9")

    def test_totals_come_back_in_base_units(self):
        oats = self.food()
        self.micro(oats, "minerals", "Iron", self.mg, "4.2")
        totals, problems = services.totals_for_food(oats)
        self.assertEqual(problems, [])
        self.assertEqual(
            totals[("micro", "Minerals", "Iron", Dimension.MASS)], Decimal("0.0042")
        )

    def test_the_same_nutrient_in_different_units_still_adds_up(self):
        """One food in mg, another in µg — the totals must not care."""
        a, b = self.food("A"), self.food("B")
        self.micro(a, "minerals", "Zinc", self.mg, "3")
        self.micro(b, "minerals", "Zinc", self.ug, "500000")

        meal = Meal.objects.create(name="Both")
        MealFood.objects.create(source_food=a, target_meal=meal)
        MealFood.objects.create(source_food=b, target_meal=meal)

        totals, _ = services.totals_for_meal(meal)
        self.assertEqual(totals[("micro", "Minerals", "Zinc", Dimension.MASS)], Decimal("3.5"))

    def test_one_food_per_source(self):
        self.food("Oats", source_api="OFF", external_id="1")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.food("Oats again", source_api="OFF", external_id="1")

    def test_hand_entered_foods_are_not_constrained(self):
        self.food("Mum's chutney")
        self.food("Dad's chutney")
        self.assertEqual(Food.objects.count(), 2)


class MealFoodTests(Seeded):
    def setUp(self):
        self.oats = self.food()
        self.macro(self.oats, "energy", "Energy", self.kcal, "400")
        self.meal = Meal.objects.create(name="Porridge")

    def test_a_portion_scales_what_it_contributes(self):
        portion = MealFood.objects.create(
            source_food=self.oats, target_meal=self.meal, portion_percentage=Decimal("50")
        )
        totals, _ = services.totals_for_portion(portion)
        self.assertEqual(totals[("macro", "Energy", "Energy", Dimension.ENERGY)], Decimal("200"))

    def test_a_row_must_point_at_something(self):
        row = MealFood(source_is_meal=False, target_meal=self.meal)
        with self.assertRaises(ValidationError):
            row.clean()

    def test_the_flag_has_to_agree_with_the_key(self):
        """A bare (bool, id) pair could not be checked. This can."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MealFood.objects.create(
                    source_is_meal=True, source_food=self.oats, target_meal=self.meal
                )

    def test_a_meal_cannot_contain_itself_directly(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MealFood.objects.create(
                    source_is_meal=True, source_meal=self.meal, target_meal=self.meal
                )

    def test_a_standalone_portion_has_no_target(self):
        portion = MealFood.objects.create(source_food=self.oats)
        self.assertIsNone(portion.target_meal)
        self.assertEqual(portion.source, self.oats)


class NestedMealTests(Seeded):
    """A meal made of meals — the case the recursion exists for."""

    def setUp(self):
        self.g_ = self.g
        self.paste = Meal.objects.create(name="Spice paste")
        self.cumin = self.food("Cumin")
        self.macro(self.cumin, "energy", "Energy", self.kcal, "100")
        MealFood.objects.create(source_food=self.cumin, target_meal=self.paste)

        self.curry = Meal.objects.create(name="Curry")
        self.lentils = self.food("Lentils")
        self.macro(self.lentils, "energy", "Energy", self.kcal, "350")
        MealFood.objects.create(source_food=self.lentils, target_meal=self.curry)

    def test_a_meal_inside_a_meal_is_counted(self):
        MealFood.objects.create(
            source_is_meal=True, source_meal=self.paste, target_meal=self.curry
        )
        totals, problems = services.totals_for_meal(self.curry)
        self.assertEqual(problems, [])
        self.assertEqual(totals[("macro", "Energy", "Energy", Dimension.ENERGY)], Decimal("450"))

    def test_portions_multiply_down_the_tree(self):
        MealFood.objects.create(
            source_is_meal=True,
            source_meal=self.paste,
            target_meal=self.curry,
            portion_percentage=Decimal("50"),
        )
        totals, _ = services.totals_for_meal(self.curry, scale=Decimal("0.5"))
        # 350 * 0.5, plus 100 * 0.5 * 0.5
        self.assertEqual(totals[("macro", "Energy", "Energy", Dimension.ENERGY)], Decimal("200"))

    def test_creating_an_indirect_cycle_is_refused(self):
        """Curry contains paste; paste must not then contain curry."""
        MealFood.objects.create(
            source_is_meal=True, source_meal=self.paste, target_meal=self.curry
        )
        loop = MealFood(source_is_meal=True, source_meal=self.curry, target_meal=self.paste)
        with self.assertRaises(ValidationError):
            loop.clean()

    def test_a_cycle_that_got_in_anyway_is_reported_not_hung(self):
        """Validation can be bypassed; an infinite loop in a page cannot be."""
        MealFood.objects.create(
            source_is_meal=True, source_meal=self.paste, target_meal=self.curry
        )
        MealFood.objects.bulk_create(
            [MealFood(source_is_meal=True, source_meal_id=self.curry.pk,
                      target_meal_id=self.paste.pk, portion_percentage=Decimal("100"))]
        )
        totals, problems = services.totals_for_meal(self.curry)
        self.assertTrue(problems)
        self.assertIn("contains itself", " ".join(problems))


class TrackingTests(Seeded):
    def setUp(self):
        self.user = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        self.oats = self.food()
        self.macro(self.oats, "energy", "Energy", self.kcal, "400")
        self.macro(self.oats, "protein", "Protein", self.g, "12")
        self.portion = MealFood.objects.create(source_food=self.oats)

    def log(self, portion="1", when=None):
        return FoodTracking.objects.create(
            user=self.user,
            meal_food=self.portion,
            portion=Decimal(portion),
            timestamp=when or timezone.now(),
        )

    def test_eating_half_counts_half(self):
        totals, _ = services.totals_for_tracking(self.log("0.5"))
        self.assertEqual(totals[("macro", "Energy", "Energy", Dimension.ENERGY)], Decimal("200"))

    def test_a_day_adds_up(self):
        self.log("1")
        self.log("0.25")
        totals, problems = services.totals_for_day(self.user, timezone.localdate())
        self.assertEqual(problems, [])
        self.assertEqual(totals[("macro", "Energy", "Energy", Dimension.ENERGY)], Decimal("500"))

    def test_yesterday_is_not_today(self):
        self.log("1", when=timezone.now() - timezone.timedelta(days=1))
        totals, _ = services.totals_for_day(self.user, timezone.localdate())
        self.assertEqual(len(totals), 0)

    def test_one_persons_log_is_not_anothers(self):
        other = get_user_model().objects.create_user("other", password="hunter2hunter2")
        self.log("1")
        totals, _ = services.totals_for_day(other, timezone.localdate())
        self.assertEqual(len(totals), 0)

    def test_a_tracked_portion_cannot_be_deleted_out_from_under_it(self):
        self.log("1")
        from django.db.models import ProtectedError

        with self.assertRaises(ProtectedError):
            self.portion.delete()

    def test_readable_rows_put_macros_first(self):
        totals, _ = services.totals_for_tracking(self.log("1"))
        rows = services.readable(totals)
        self.assertEqual(rows[0]["kind"], "macro")
        self.assertEqual({row["unit"] for row in rows}, {"kcal", "g"})


class ImportTests(Seeded):
    """Moving the old wide rows across without inventing anything."""

    def old_food(self, **kwargs):
        from pantry.models import FoodEntry

        return FoodEntry.objects.create(**kwargs)

    def test_one_column_becomes_one_row(self):
        self.old_food(
            name="Fortified oats",
            brand="Flahavan's",
            energy_kcal=Decimal("372"),
            protein_g=Decimal("11.2"),
            iron_mg=Decimal("4.2"),
            folate_ug=Decimal("110"),
        )
        call_command("import_foodentries", verbosity=0)

        food = Food.objects.get(name="Fortified oats")
        self.assertEqual(food.description, "Flahavan's")
        self.assertEqual(food.macros.count(), 2)
        self.assertEqual(food.micros.count(), 2)

    def test_a_null_column_becomes_no_row_at_all(self):
        self.old_food(name="Mystery", energy_kcal=Decimal("100"))
        call_command("import_foodentries", verbosity=0)
        food = Food.objects.get(name="Mystery")
        self.assertEqual(food.micros.count(), 0)
        self.assertEqual(food.macros.count(), 1)

    def test_units_survive_the_move(self):
        self.old_food(name="Oats", iron_mg=Decimal("4.2"), folate_ug=Decimal("110"))
        call_command("import_foodentries", verbosity=0)
        food = Food.objects.get(name="Oats")
        self.assertEqual(food.micros.get(micro_name="Iron").unit.symbol, "mg")
        self.assertEqual(food.micros.get(micro_name="Folate").unit.symbol, "\u00b5g")

    def test_the_numbers_are_unchanged(self):
        self.old_food(name="Oats", iron_mg=Decimal("4.2"))
        call_command("import_foodentries", verbosity=0)
        totals, _ = services.totals_for_food(Food.objects.get(name="Oats"))
        self.assertEqual(
            totals[("micro", "Minerals", "Iron", Dimension.MASS)], Decimal("0.0042")
        )

    def test_importing_twice_does_not_duplicate(self):
        self.old_food(name="Oats", energy_kcal=Decimal("372"), source_api="OFF", external_id="7")
        call_command("import_foodentries", verbosity=0)
        call_command("import_foodentries", verbosity=0)
        self.assertEqual(Food.objects.filter(name="Oats").count(), 1)
        self.assertEqual(Food.objects.get(name="Oats").macros.count(), 1)

    def test_a_dry_run_writes_nothing(self):
        self.old_food(name="Oats", energy_kcal=Decimal("372"))
        call_command("import_foodentries", "--dry-run", verbosity=0)
        self.assertEqual(Food.objects.count(), 0)


class PortionFromRecipeTests(Seeded):
    """Turning a recipe line into a portion of a food.

    The point of computing rather than storing: the same food in a recipe
    calling for 250 g and one calling for 2 tbsp is two different portions.
    """

    def setUp(self):
        self.oats = self.food("Rolled oats")
        self.macro(self.oats, "energy", "Energy", self.kcal, "372")

    def factor(self, quantity, unit, food=None):
        return services.portion_factor(Decimal(quantity), unit, food or self.oats)

    def test_the_same_unit_just_divides(self):
        factor, problem = self.factor("250", "g")
        self.assertIsNone(problem)
        self.assertEqual(factor, Decimal("2.5"))

    def test_a_bigger_unit_converts_first(self):
        factor, _ = self.factor("1", "kg")
        self.assertEqual(factor, Decimal("10"))

    def test_an_imperial_unit_converts(self):
        factor, _ = self.factor("8", "oz")
        self.assertAlmostEqual(float(factor), 2.26795, places=4)

    def test_a_different_reference_quantity_is_honoured(self):
        bar = Food.objects.create(
            name="Cereal bar", reference_unit=self.g, reference_quantity=Decimal("30")
        )
        self.macro(bar, "energy", "Energy", self.kcal, "120")
        factor, _ = services.portion_factor(Decimal("100"), "g", bar)
        self.assertAlmostEqual(float(factor), 3.3333, places=4)

    def test_volume_needs_a_density_and_says_so(self):
        factor, problem = self.factor("2", "tbsp")
        self.assertIsNone(factor)
        self.assertIn("density", problem)

    def test_with_a_density_a_volume_becomes_a_weight(self):
        oil = Food.objects.create(
            name="Olive oil", reference_unit=self.g, density_g_per_ml=Decimal("0.92")
        )
        amount, problem = services.to_reference_unit(
            Decimal("2"), Unit.objects.get(symbol="tbsp"), oil
        )
        self.assertIsNone(problem)
        # 2 tbsp is 29.57 ml; at 0.92 g/ml that is 27.2 g.
        self.assertAlmostEqual(float(amount), 27.208, places=2)

    def test_flour_by_the_cup(self):
        flour = Food.objects.create(
            name="Plain flour", reference_unit=self.g, density_g_per_ml=Decimal("0.53")
        )
        amount, _ = services.to_reference_unit(
            Decimal("1"), Unit.objects.get(symbol="cup"), flour
        )
        self.assertAlmostEqual(float(amount), 125.39, places=1)

    def test_counting_needs_a_recorded_weight(self):
        garlic = Food.objects.create(name="Garlic", reference_unit=self.g)
        factor, problem = services.portion_factor(Decimal("3"), "clove", garlic)
        self.assertIsNone(factor)
        self.assertIn("weight recorded", problem)

    def test_with_a_weight_counting_works(self):
        from nutrition.models import FoodPortion

        garlic = Food.objects.create(name="Garlic", reference_unit=self.g)
        FoodPortion.objects.create(
            food=garlic, unit=Unit.objects.get(symbol="clove"), grams=Decimal("4")
        )
        amount, problem = services.to_reference_unit(
            Decimal("3"), Unit.objects.get(symbol="clove"), garlic
        )
        self.assertIsNone(problem)
        self.assertEqual(amount, Decimal("12"))

    def test_two_volumes_need_no_density(self):
        milk = Food.objects.create(
            name="Milk", reference_unit=Unit.objects.get(symbol="ml")
        )
        amount, problem = services.to_reference_unit(
            Decimal("1"), Unit.objects.get(symbol="cup"), milk
        )
        self.assertIsNone(problem)
        self.assertAlmostEqual(float(amount), 236.588, places=2)

    def test_a_missing_amount_is_a_gap_not_a_zero(self):
        factor, problem = services.portion_factor(None, "g", self.oats)
        self.assertIsNone(factor)
        self.assertIn("no amount", problem)

    def test_a_missing_unit_is_a_gap(self):
        factor, problem = services.portion_factor(Decimal("2"), "", self.oats)
        self.assertIsNone(factor)
        self.assertIn("no unit", problem)

    def test_the_nutrition_follows_the_factor(self):
        totals, problems = services.totals_for_line(Decimal("250"), "g", self.oats)
        self.assertEqual(problems, [])
        self.assertEqual(
            totals[("macro", "Energy", "Energy", Dimension.ENERGY)], Decimal("930")
        )

    def test_a_gap_reports_rather_than_returning_a_wrong_number(self):
        totals, problems = services.totals_for_line(Decimal("2"), "tbsp", self.oats)
        self.assertEqual(len(totals), 0)
        self.assertTrue(problems)

    def test_every_unit_a_recipe_can_use_exists_here(self):
        """A recipe unit with no nutrition row would be a gap for the wrong reason."""
        from recipes.models import Unit as RecipeUnit

        symbols = {value for value, _ in RecipeUnit.choices if value}
        known = set(Unit.objects.values_list("symbol", flat=True))
        self.assertEqual(symbols - known, set())
