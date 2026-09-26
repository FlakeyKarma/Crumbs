from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from recipes.models import Ingredient, Recipe, RecipeIngredient, Step, Tag


class RecipeModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cook = get_user_model().objects.create_user("cook", password="x")

    def test_slug_is_generated_and_kept_unique(self):
        first = Recipe.objects.create(title="Roast chicken", author=self.cook)
        second = Recipe.objects.create(title="Roast chicken", author=self.cook)
        self.assertEqual(first.slug, "roast-chicken")
        self.assertEqual(second.slug, "roast-chicken-2")

    def test_total_minutes_adds_what_is_known(self):
        recipe = Recipe.objects.create(
            title="Stew", author=self.cook, prep_minutes=15, cook_minutes=90
        )
        self.assertEqual(recipe.total_minutes, 105)

    def test_total_minutes_is_none_when_nothing_is_stated(self):
        recipe = Recipe.objects.create(title="Toast", author=self.cook)
        self.assertIsNone(recipe.total_minutes)

    def test_scale_factor(self):
        recipe = Recipe.objects.create(title="Dal", author=self.cook, servings=4)
        self.assertEqual(recipe.scale_factor(2), Decimal("0.5"))
        self.assertEqual(recipe.scale_factor(4), Decimal("1"))

    def test_ingredient_groups_scale_and_keep_the_main_group_first(self):
        recipe = Recipe.objects.create(title="Dal", author=self.cook, servings=4)
        RecipeIngredient.objects.create(
            recipe=recipe,
            ingredient=Ingredient.from_name("red lentils"),
            quantity=Decimal("250"),
            unit="g",
            position=0,
        )
        RecipeIngredient.objects.create(
            recipe=recipe,
            ingredient=Ingredient.from_name("ghee"),
            quantity=Decimal("2"),
            unit="tbsp",
            group="For the tempering",
            position=1,
        )

        groups = recipe.ingredient_groups(recipe.scale_factor(2))
        self.assertEqual([heading for heading, _ in groups], ["", "For the tempering"])
        self.assertEqual(groups[0][1][0]["quantity"], "125")
        self.assertEqual(groups[1][1][0]["quantity"], "1")

    def test_mark_cooked_counts_up(self):
        from django.utils import timezone

        recipe = Recipe.objects.create(title="Dal", author=self.cook)
        recipe.mark_cooked(timezone.localdate())
        recipe.mark_cooked(timezone.localdate())
        recipe.refresh_from_db()
        self.assertEqual(recipe.times_cooked, 2)
        self.assertEqual(recipe.last_cooked, timezone.localdate())

    def test_ingredient_names_are_reused_case_insensitively(self):
        first = Ingredient.from_name("Smoked Paprika")
        second = Ingredient.from_name("smoked paprika")
        self.assertEqual(first.pk, second.pk)

    def test_tags_are_normalised_to_lowercase(self):
        self.assertEqual(Tag.from_name("  Weeknight  ").name, "weeknight")
        self.assertEqual(Tag.from_name("WEEKNIGHT").pk, Tag.from_name("weeknight").pk)

    def test_steps_come_back_in_position_order(self):
        recipe = Recipe.objects.create(title="Dal", author=self.cook)
        Step.objects.create(recipe=recipe, position=1, text="Second")
        Step.objects.create(recipe=recipe, position=0, text="First")
        self.assertEqual([s.text for s in recipe.steps.all()], ["First", "Second"])


class VisibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user("owner", password="x")
        cls.other = User.objects.create_user("other", password="x")
        cls.private = Recipe.objects.create(
            title="Private", author=cls.owner, is_shared=False
        )
        cls.shared = Recipe.objects.create(
            title="Shared", author=cls.owner, is_shared=True
        )

    def test_anonymous_sees_only_shared(self):
        from django.contrib.auth.models import AnonymousUser

        visible = Recipe.objects.visible_to(AnonymousUser())
        self.assertEqual(list(visible), [self.shared])

    def test_owner_sees_their_own_private_recipes(self):
        visible = Recipe.objects.visible_to(self.owner)
        self.assertCountEqual(visible, [self.private, self.shared])

    def test_other_users_do_not_see_someone_elses_private_recipes(self):
        visible = Recipe.objects.visible_to(self.other)
        self.assertEqual(list(visible), [self.shared])
