"""Tests for the Health Panel.

The panel is mostly arithmetic over three moving parts: what a target says,
what was logged, and whether logging should move stock. Each is tested where
it makes a decision rather than where it renders.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from health import services
from health.models import (
    ConsumptionEntry,
    HealthTarget,
    Metric,
    MetricKind,
    Observation,
    TargetMode,
)
from pantry import services as pantry
from pantry.models import FoodEntry, IngredientDefault, MovementKind, PantryItem
from recipes.models import Ingredient, Recipe, RecipeIngredient


class EvaluateTests(TestCase):
    """One vocabulary across four target shapes, so the bar draws one way."""

    def test_a_floor_is_met_or_under(self):
        self.assertEqual(
            services.evaluate(Decimal("80"), Decimal("60"), None, TargetMode.FLOOR)["state"],
            "ok",
        )
        self.assertEqual(
            services.evaluate(Decimal("40"), Decimal("60"), None, TargetMode.FLOOR)["state"],
            "under",
        )

    def test_a_ceiling_is_met_or_over(self):
        self.assertEqual(
            services.evaluate(Decimal("1800"), None, Decimal("2000"), TargetMode.CEILING)["state"],
            "ok",
        )
        self.assertEqual(
            services.evaluate(Decimal("2400"), None, Decimal("2000"), TargetMode.CEILING)["state"],
            "over",
        )

    def test_a_range_has_both_edges(self):
        for value, expected in ((Decimal("50"), "under"), (Decimal("150"), "ok"),
                                (Decimal("250"), "over")):
            with self.subTest(value=value):
                result = services.evaluate(
                    value, Decimal("100"), Decimal("200"), TargetMode.RANGE
                )
                self.assertEqual(result["state"], expected)

    def test_nothing_logged_is_unknown_not_zero(self):
        result = services.evaluate(None, Decimal("60"), None, TargetMode.FLOOR)
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["fraction"])

    def test_track_only_never_judges(self):
        result = services.evaluate(Decimal("72"), None, None, TargetMode.TRACK)
        self.assertEqual(result["state"], "tracked")

    def test_the_bar_fills_but_does_not_overflow(self):
        result = services.evaluate(Decimal("4000"), None, Decimal("2000"), TargetMode.CEILING)
        self.assertEqual(result["fraction"], 1.0)

    def test_a_mode_without_its_bound_degrades_to_tracking(self):
        self.assertEqual(
            services.evaluate(Decimal("80"), None, None, TargetMode.FLOOR)["state"], "tracked"
        )


class SeedTests(TestCase):
    def test_the_seed_command_ships_metrics_and_is_repeatable(self):
        call_command("seed_health_metrics", verbosity=0)
        first = Metric.objects.count()
        self.assertGreater(first, 0)
        call_command("seed_health_metrics", verbosity=0)
        self.assertEqual(Metric.objects.count(), first)

    def test_built_in_metrics_belong_to_nobody(self):
        call_command("seed_health_metrics", verbosity=0)
        self.assertFalse(Metric.objects.filter(owner__isnull=False).exists())
        self.assertTrue(Metric.objects.filter(kind=MetricKind.NUTRITION).exists())


class TargetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        call_command("seed_health_metrics", verbosity=0)
        cls.metric = Metric.objects.filter(kind=MetricKind.NUTRITION).first()

    def test_a_target_is_effective_dated_rather_than_edited(self):
        today = timezone.localdate()
        HealthTarget.objects.create(
            user=self.user, metric=self.metric, mode=TargetMode.FLOOR,
            lower=Decimal("60"), effective_from=today - timedelta(days=30),
        )
        HealthTarget.objects.create(
            user=self.user, metric=self.metric, mode=TargetMode.FLOOR,
            lower=Decimal("90"), effective_from=today,
        )
        old = services.target_on(self.user, self.metric, today - timedelta(days=10))
        new = services.target_on(self.user, self.metric, today)
        self.assertEqual(old.lower, Decimal("60"))
        self.assertEqual(new.lower, Decimal("90"))
        self.assertEqual(HealthTarget.objects.count(), 2)

    def test_no_target_before_the_first_one_starts(self):
        HealthTarget.objects.create(
            user=self.user, metric=self.metric, mode=TargetMode.FLOOR,
            lower=Decimal("60"), effective_from=timezone.localdate(),
        )
        self.assertIsNone(
            services.target_on(self.user, self.metric, timezone.localdate() - timedelta(days=1))
        )


class LoggingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        cls.food = FoodEntry.objects.create(
            name="Red lentils",
            energy_kcal=Decimal("352"),
            protein_g=Decimal("24.6"),
            carbohydrate_g=Decimal("63.1"),
            fat_g=Decimal("1.1"),
        )

    def stocked(self, grams):
        item = PantryItem.objects.create(owner=self.user, entry=self.food)
        pantry.record(item, MovementKind.ADD, Decimal(grams))
        return item

    def test_eating_a_food_takes_it_out_of_the_cupboard(self):
        self.stocked(500)
        services.log_food(self.user, self.food, grams=Decimal("100"))
        self.assertEqual(pantry.stock_of(self.user, self.food), Decimal("400"))

    def test_eating_is_recorded_as_eaten_not_as_cooking(self):
        self.stocked(500)
        services.log_food(self.user, self.food, grams=Decimal("100"))
        from pantry.models import StockMovement

        kinds = set(StockMovement.objects.values_list("kind", flat=True))
        self.assertIn(MovementKind.EATEN, kinds)
        self.assertNotIn(MovementKind.CONSUME, kinds)

    def test_nutrients_are_frozen_on_the_entry(self):
        entry = services.log_food(self.user, self.food, grams=Decimal("100"))
        self.assertEqual(Decimal(entry.nutrients["energy_kcal"]), Decimal("352.000"))

        self.food.energy_kcal = Decimal("999")
        self.food.save()
        entry.refresh_from_db()
        self.assertEqual(Decimal(entry.nutrients["energy_kcal"]), Decimal("352.000"))

    def test_eating_a_recipe_does_not_empty_the_cupboard_twice(self):
        """Cooking already drew the ingredients; the portion is from the fridge."""
        self.stocked(500)
        lentils = Ingredient.from_name("red lentils")
        IngredientDefault.objects.create(user=self.user, ingredient=lentils, entry=self.food)
        recipe = Recipe.objects.create(title="Dal", author=self.user, servings=4)
        RecipeIngredient.objects.create(
            recipe=recipe, ingredient=lentils, quantity=Decimal("400"), unit="g"
        )

        pantry.cook_from_recipe(recipe, self.user)
        self.assertEqual(pantry.stock_of(self.user, self.food), Decimal("100"))

        services.log_recipe(self.user, recipe, servings=Decimal("1"))
        self.assertEqual(pantry.stock_of(self.user, self.food), Decimal("100"))

    def test_a_portion_is_a_share_of_the_batch(self):
        lentils = Ingredient.from_name("red lentils")
        IngredientDefault.objects.create(user=self.user, ingredient=lentils, entry=self.food)
        recipe = Recipe.objects.create(title="Dal", author=self.user, servings=4)
        RecipeIngredient.objects.create(
            recipe=recipe, ingredient=lentils, quantity=Decimal("400"), unit="g"
        )
        entry, problems = services.log_recipe(self.user, recipe, servings=Decimal("1"))
        self.assertEqual(problems, [])
        # 400 g of lentils is 1408 kcal for the batch, a quarter of it eaten.
        self.assertAlmostEqual(
            Decimal(entry.nutrients["energy_kcal"]), Decimal("352.000"), places=2
        )

    def test_a_recipe_with_an_unresolved_line_is_logged_and_flagged(self):
        recipe = Recipe.objects.create(title="Mystery", author=self.user, servings=2)
        RecipeIngredient.objects.create(
            recipe=recipe,
            ingredient=Ingredient.from_name("something"),
            quantity=Decimal("100"),
            unit="g",
        )
        entry, problems = services.log_recipe(self.user, recipe)
        self.assertTrue(entry.incomplete)
        self.assertEqual(len(problems), 1)


class DayReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        call_command("seed_health_metrics", verbosity=0)
        cls.food = FoodEntry.objects.create(name="Oats", energy_kcal=Decimal("380"))

    def test_an_empty_day_reports_rather_than_failing(self):
        report = services.day_report(self.user)
        self.assertIsNotNone(report)

    def test_what_was_eaten_reaches_the_day(self):
        services.log_food(self.user, self.food, grams=Decimal("100"), deplete_stock=False)
        self.assertEqual(ConsumptionEntry.objects.count(), 1)
        report = services.day_report(self.user)
        self.assertIsNotNone(report)

    def test_yesterdays_food_is_not_todays(self):
        services.log_food(
            self.user,
            self.food,
            grams=Decimal("100"),
            consumed_at=timezone.now() - timedelta(days=1),
            deplete_stock=False,
        )
        today = services.day_report(self.user, timezone.localdate())
        yesterday = services.day_report(self.user, timezone.localdate() - timedelta(days=1))
        self.assertNotEqual(today, yesterday)


class AccessTests(TestCase):
    def test_the_panel_needs_an_account(self):
        for name in ("health:panel", "health:targets", "health:preferences"):
            with self.subTest(view=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response["Location"])

    def test_signed_in_readers_get_the_panel(self):
        get_user_model().objects.create_user("cook", password="hunter2hunter2")
        call_command("seed_health_metrics", verbosity=0)
        self.client.login(username="cook", password="hunter2hunter2")
        self.assertEqual(self.client.get(reverse("health:panel")).status_code, 200)

    def test_one_persons_log_is_not_anothers(self):
        User = get_user_model()
        mine = User.objects.create_user("mine", password="hunter2hunter2")
        yours = User.objects.create_user("yours", password="hunter2hunter2")
        food = FoodEntry.objects.create(name="Oats", energy_kcal=Decimal("380"))
        services.log_food(mine, food, grams=Decimal("100"), deplete_stock=False)
        self.assertEqual(ConsumptionEntry.objects.filter(user=yours).count(), 0)
        self.assertEqual(ConsumptionEntry.objects.filter(user=mine).count(), 1)


class ObservationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        call_command("seed_health_metrics", verbosity=0)

    def test_a_weight_reading_is_stored_against_its_metric(self):
        metric = Metric.objects.filter(kind=MetricKind.BODY).first()
        if metric is None:
            self.skipTest("no body metric in the seed set")
        Observation.objects.create(
            user=self.user, metric=metric, value=Decimal("72.4"),
            observed_at=timezone.now(),
        )
        self.assertEqual(Observation.objects.filter(user=self.user).count(), 1)


class TemplateTests(TestCase):
    """A partial gets its own tag library — {% load %} does not reach into an
    include, and the panel rendered fine right up until a row existed."""

    def test_the_measure_partial_renders_on_its_own(self):
        from django.template.loader import render_to_string

        html = render_to_string(
            "health/_measure.html",
            {
                "row": {
                    "metric": type("M", (), {"name": "Protein", "unit": "g", "pk": 1})(),
                    "display": "82",
                    "state": "ok",
                    "value": Decimal("82"),
                    "lower": Decimal("60"),
                    "upper": None,
                    "fraction": 0.8,
                    "target": None,
                    "cadence": "daily",
                }
            },
        )
        self.assertIn("measure__band", html)
        self.assertIn("Protein", html)

    def test_the_panel_renders_with_a_metric_present(self):
        user = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        call_command("seed_health_metrics", verbosity=0)
        self.client.force_login(user)
        response = self.client.get(reverse("health:panel"))
        self.assertEqual(response.status_code, 200)


class NutrientMetricTests(TestCase):
    """Every pantry nutrient has to be targetable, or logging records numbers
    the panel can never show."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_health_metrics", verbosity=0)

    def test_every_nutrient_has_a_metric(self):
        from pantry.models import FoodEntry

        mapped = set(
            Metric.objects.exclude(nutrient_field="").values_list("nutrient_field", flat=True)
        )
        self.assertEqual(mapped, set(FoodEntry.NUTRIENTS))

    def test_no_two_metrics_read_the_same_nutrient(self):
        fields = list(
            Metric.objects.exclude(nutrient_field="").values_list("nutrient_field", flat=True)
        )
        self.assertEqual(len(fields), len(set(fields)))

    def test_metric_units_match_the_field_they_read(self):
        """A field named _ug showing "mg" would be wrong by a thousand."""
        from pantry.models import nutrient_unit

        for metric in Metric.objects.exclude(nutrient_field=""):
            with self.subTest(metric=metric.slug):
                self.assertEqual(metric.unit, nutrient_unit(metric.nutrient_field))

    def test_micronutrients_keep_enough_decimal_places(self):
        """1.4 mg of B6 rounded to a whole number is out by a third."""
        for slug in ("thiamin", "riboflavin", "vitamin-b6"):
            with self.subTest(slug=slug):
                self.assertGreaterEqual(Metric.objects.get(slug=slug).decimals, 2)

    def test_groups_follow_the_pantry(self):
        self.assertEqual(services.group_of(Metric.objects.get(slug="protein")), "Macronutrients")
        self.assertEqual(services.group_of(Metric.objects.get(slug="iron")), "Minerals")
        self.assertEqual(services.group_of(Metric.objects.get(slug="folate")), "Vitamins")

    def test_a_non_nutrient_metric_falls_back_to_its_kind(self):
        self.assertNotIn(
            services.group_of(Metric.objects.get(slug="weight")),
            {"Macronutrients", "Minerals", "Vitamins"},
        )

    def test_reseeding_does_not_duplicate_the_new_metrics(self):
        before = Metric.objects.count()
        call_command("seed_health_metrics", verbosity=0)
        self.assertEqual(Metric.objects.count(), before)


class GroupedPanelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        call_command("seed_health_metrics", verbosity=0)
        cls.food = FoodEntry.objects.create(
            name="Fortified oats",
            energy_kcal=Decimal("372"),
            protein_g=Decimal("11.2"),
            iron_mg=Decimal("4.2"),
            folate_ug=Decimal("110"),
        )

    def test_the_panel_groups_its_measures(self):
        report = services.day_report(self.user)
        labels = [group["label"] for group in report["groups"]]
        for expected in ("Macronutrients", "Minerals", "Vitamins"):
            self.assertIn(expected, labels)
        self.assertEqual(labels[:3], ["Macronutrients", "Minerals", "Vitamins"])

    def test_a_targeted_measure_stays_out_in_the_open(self):
        HealthTarget.objects.create(
            user=self.user,
            metric=Metric.objects.get(slug="iron"),
            mode=TargetMode.FLOOR,
            lower=Decimal("8"),
            effective_from=timezone.localdate(),
        )
        groups = {g["label"]: g for g in services.day_report(self.user)["groups"]}
        tracked = [row["metric"].slug for row in groups["Minerals"]["tracked"]]
        self.assertEqual(tracked, ["iron"])
        self.assertNotIn("iron", [row["metric"].slug for row in groups["Minerals"]["untracked"]])

    def test_untargeted_nutrients_are_still_totalled(self):
        services.log_food(self.user, self.food, grams=Decimal("100"), deplete_stock=False)
        groups = {g["label"]: g for g in services.day_report(self.user)["groups"]}
        iron = [r for r in groups["Minerals"]["rows"] if r["metric"].slug == "iron"][0]
        folate = [r for r in groups["Vitamins"]["rows"] if r["metric"].slug == "folate"][0]
        self.assertEqual(iron["value"], Decimal("4.200"))
        self.assertEqual(folate["value"], Decimal("110.000"))

    def test_every_metric_lands_in_exactly_one_group(self):
        report = services.day_report(self.user)
        grouped = sum(len(group["rows"]) for group in report["groups"])
        self.assertEqual(grouped, len(report["rows"]))

    def test_the_panel_still_renders_with_them_all(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("health:panel"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vitamins")


class FrozenBreakdownTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        cls.food = FoodEntry.objects.create(
            name="Fortified oats", energy_kcal=Decimal("372"), iron_mg=Decimal("4.2")
        )

    def test_logging_freezes_the_micronutrients_too(self):
        entry = services.log_food(
            self.user, self.food, grams=Decimal("50"), deplete_stock=False
        )
        self.assertEqual(Decimal(entry.nutrients["iron_mg"]), Decimal("2.100"))

    def test_the_breakdown_groups_what_was_recorded(self):
        entry = services.log_food(
            self.user, self.food, grams=Decimal("100"), deplete_stock=False
        )
        table = {group["label"]: group for group in entry.nutrition_table()}
        self.assertEqual([r["field"] for r in table["Minerals"]["recorded"]], ["iron_mg"])
        self.assertEqual(table["Vitamins"]["recorded"], [])

    def test_the_breakdown_reads_the_frozen_copy_not_the_food(self):
        entry = services.log_food(
            self.user, self.food, grams=Decimal("100"), deplete_stock=False
        )
        self.food.iron_mg = Decimal("99")
        self.food.save()
        table = {group["label"]: group for group in entry.nutrition_table()}
        self.assertEqual(Decimal(table["Minerals"]["recorded"][0]["value"]), Decimal("4.200"))


class MacroPlanTests(TestCase):
    """Calories and macros are two views of one thing; the plan picks which
    one you set and derives the other."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        call_command("seed_health_metrics", verbosity=0)

    def plan(self, **kwargs):
        from health.models import MacroPlan

        defaults = {"mode": "calories", "split": "maintenance"}
        defaults.update(kwargs)
        return MacroPlan.objects.create(user=self.user, **defaults)

    def test_off_by_default_so_nothing_is_decided_for_you(self):
        self.assertEqual(services.plan_targets(self.user), {})

    def test_calories_split_into_grams(self):
        self.plan(energy_lower=Decimal("2000"), energy_upper=Decimal("2400"))
        decided = services.plan_targets(self.user)
        # 25% of 2000 kcal is 500 kcal; protein is 4 kcal a gram.
        self.assertEqual(decided["protein"]["lower"], Decimal("125.0"))
        # 25% of 2400 is 600 kcal; fat is 9 a gram.
        self.assertEqual(decided["fat"]["upper"], Decimal("66.7"))
        self.assertEqual(decided["carbohydrate"]["lower"], Decimal("250.0"))

    def test_each_published_split_gives_its_own_grams(self):
        from health.models import MacroPlan

        wanted = {
            "maintenance": Decimal("125.0"),
            "fat-loss": Decimal("150.0"),
            "muscle-gain": Decimal("150.0"),
        }
        for split, protein_low in wanted.items():
            with self.subTest(split=split):
                MacroPlan.objects.filter(user=self.user).delete()
                self.plan(
                    split=split, energy_lower=Decimal("2000"), energy_upper=Decimal("2400")
                )
                self.assertEqual(
                    services.plan_targets(self.user)["protein"]["lower"], protein_low
                )

    def test_the_energy_bound_is_the_one_you_typed(self):
        self.plan(energy_lower=Decimal("2000"), energy_upper=Decimal("2400"))
        decided = services.plan_targets(self.user)
        self.assertEqual(decided["energy"]["lower"], Decimal("2000.0"))
        self.assertEqual(decided["energy"]["upper"], Decimal("2400.0"))

    def test_the_other_direction_adds_macros_up(self):
        from health.models import HealthTarget, Metric, TargetMode

        self.plan(mode="macros")
        for slug, grams in (("protein", "150"), ("carbohydrate", "200"), ("fat", "60")):
            HealthTarget.objects.create(
                user=self.user,
                metric=Metric.objects.get(slug=slug),
                mode=TargetMode.FLOOR,
                lower=Decimal(grams),
                effective_from=timezone.localdate(),
            )
        # 150*4 + 200*4 + 60*9 = 1940
        self.assertEqual(
            services.plan_targets(self.user)["energy"]["lower"], Decimal("1940.0")
        )

    def test_adding_up_says_which_macro_had_no_target(self):
        from health.models import HealthTarget, Metric, TargetMode

        self.plan(mode="macros")
        HealthTarget.objects.create(
            user=self.user,
            metric=Metric.objects.get(slug="protein"),
            mode=TargetMode.FLOOR,
            lower=Decimal("150"),
            effective_from=timezone.localdate(),
        )
        note = services.plan_targets(self.user)["energy"]["note"]
        self.assertIn("carbohydrate", note)
        self.assertIn("fat", note)

    def test_adding_up_nothing_decides_nothing(self):
        self.plan(mode="macros")
        self.assertEqual(services.plan_targets(self.user), {})

    def test_custom_percentages_are_used_when_chosen(self):
        self.plan(
            split="custom",
            energy_lower=Decimal("2000"),
            energy_upper=Decimal("2000"),
            protein_lower=Decimal("40"),
            protein_upper=Decimal("40"),
            carbohydrate_lower=Decimal("30"),
            carbohydrate_upper=Decimal("40"),
            fat_lower=Decimal("30"),
            fat_upper=Decimal("30"),
        )
        self.assertEqual(
            services.plan_targets(self.user)["protein"]["lower"], Decimal("200.0")
        )

    def test_a_preset_ignores_the_custom_boxes(self):
        self.plan(
            split="maintenance",
            energy_lower=Decimal("2000"),
            energy_upper=Decimal("2000"),
            protein_lower=Decimal("90"),
            protein_upper=Decimal("90"),
        )
        self.assertEqual(
            services.plan_targets(self.user)["protein"]["lower"], Decimal("125.0")
        )

    def test_every_published_split_is_satisfiable(self):
        """Lows must total 100 or less and highs 100 or more, or no set of
        grams can meet all three at once."""
        from health.models import SPLIT_PRESETS

        for split, shares in SPLIT_PRESETS.items():
            with self.subTest(split=split):
                lows = sum(low for low, _ in shares)
                highs = sum(high for _, high in shares)
                self.assertLessEqual(lows, 100)
                self.assertGreaterEqual(highs, 100)


class MacroPlanValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("cook", password="hunter2hunter2")

    def build(self, **kwargs):
        from health.models import MacroPlan

        defaults = {"user": self.user, "mode": "calories", "split": "custom",
                    "energy_lower": Decimal("2000"), "energy_upper": Decimal("2400")}
        defaults.update(kwargs)
        return MacroPlan(**defaults)

    def test_splitting_calories_needs_a_calorie_range(self):
        with self.assertRaises(ValidationError):
            self.build(energy_lower=None, energy_upper=None).clean()

    def test_an_upside_down_calorie_range_is_refused(self):
        with self.assertRaises(ValidationError):
            self.build(energy_lower=Decimal("2400"), energy_upper=Decimal("2000")).clean()

    def test_lows_adding_over_a_hundred_are_refused(self):
        with self.assertRaises(ValidationError):
            self.build(
                protein_lower=Decimal("40"), carbohydrate_lower=Decimal("40"),
                fat_lower=Decimal("40"),
            ).clean()

    def test_highs_adding_under_a_hundred_are_refused(self):
        with self.assertRaises(ValidationError):
            self.build(
                protein_upper=Decimal("20"), carbohydrate_upper=Decimal("20"),
                fat_upper=Decimal("20"),
            ).clean()

    def test_a_macro_whose_upper_is_below_its_lower_is_refused(self):
        with self.assertRaises(ValidationError):
            self.build(protein_lower=Decimal("40"), protein_upper=Decimal("20")).clean()

    def test_a_preset_is_not_held_to_the_custom_boxes(self):
        self.build(split="maintenance", protein_lower=Decimal("99")).clean()


class PanelLayoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        call_command("seed_health_metrics", verbosity=0)

    def setUp(self):
        self.client.force_login(self.user)

    def test_energy_is_pulled_out_of_the_groups(self):
        report = services.day_report(self.user)
        self.assertIsNotNone(report["energy_row"])
        self.assertEqual(report["energy_row"]["metric"].slug, "energy")
        grouped = [
            row["metric"].slug for group in report["groups"] for row in group["rows"]
        ]
        self.assertNotIn("energy", grouped)

    def test_every_other_metric_still_lands_in_a_group(self):
        report = services.day_report(self.user)
        grouped = sum(len(group["rows"]) for group in report["groups"])
        self.assertEqual(grouped, len(report["rows"]) - 1)

    def test_the_calorie_bar_is_drawn_first(self):
        body = self.client.get(reverse("health:panel")).content.decode()
        self.assertIn("energy-bar", body)
        self.assertLess(body.index("energy-bar"), body.index("measure-group"))

    def test_the_groups_are_collapsible(self):
        body = self.client.get(reverse("health:panel")).content.decode()
        self.assertIn('<details class="measure-group"', body)

    def test_a_planned_bound_reaches_the_panel(self):
        from health.models import MacroPlan

        MacroPlan.objects.create(
            user=self.user, mode="calories", split="maintenance",
            energy_lower=Decimal("2000"), energy_upper=Decimal("2400"),
        )
        rows = {row["metric"].slug: row for row in services.day_report(self.user)["rows"]}
        self.assertTrue(rows["protein"]["planned"])
        self.assertEqual(rows["protein"]["lower"], Decimal("125.0"))
        self.assertIn("% of calories", rows["protein"]["note"])

    def test_the_plan_page_loads_and_shows_the_splits(self):
        response = self.client.get(reverse("health:plan"))
        self.assertEqual(response.status_code, 200)
        for label in ("maintenance", "Fat loss", "Muscle gain"):
            self.assertContains(response, label, status_code=200)

    def test_saving_a_plan_from_the_page(self):
        from health.models import MacroPlan

        self.client.post(
            reverse("health:plan"),
            {
                "mode": "calories", "split": "fat-loss",
                "energy_lower": "1800", "energy_upper": "2100",
                "protein_lower": "25", "protein_upper": "30",
                "carbohydrate_lower": "50", "carbohydrate_upper": "55",
                "fat_lower": "15", "fat_upper": "25",
            },
        )
        self.assertEqual(MacroPlan.objects.get(user=self.user).split, "fat-loss")

    def test_the_plan_page_needs_an_account(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("health:plan")).status_code, 302)
