from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from recipes.models import Ingredient, Recipe, RecipeIngredient, Step


class IndexTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user("owner", password="hunter2hunter2")
        cls.shared = Recipe.objects.create(
            title="Weeknight dal", author=cls.owner, is_shared=True, summary="Lentils."
        )
        cls.private = Recipe.objects.create(
            title="Secret sauce", author=cls.owner, is_shared=False
        )
        RecipeIngredient.objects.create(
            recipe=cls.shared,
            ingredient=Ingredient.from_name("red lentils"),
            quantity=Decimal("250"),
            unit="g",
        )

    def test_index_lists_shared_recipes_to_anyone(self):
        response = self.client.get(reverse("recipes:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Weeknight dal")
        self.assertNotContains(response, "Secret sauce")

    def test_index_shows_your_own_private_recipes_once_signed_in(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("recipes:list"))
        self.assertContains(response, "Secret sauce")

    def test_search_matches_on_ingredient_name(self):
        response = self.client.get(reverse("recipes:list"), {"q": "lentils"})
        self.assertContains(response, "Weeknight dal")

    def test_search_with_no_matches_is_not_an_error(self):
        response = self.client.get(reverse("recipes:list"), {"q": "zzzzz"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nothing matches that")

    def test_unknown_sort_falls_back_instead_of_breaking(self):
        response = self.client.get(reverse("recipes:list"), {"sort": "; DROP TABLE"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sort"], "recent")


class DetailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user("owner", password="hunter2hunter2")
        cls.stranger = User.objects.create_user("stranger", password="hunter2hunter2")
        cls.recipe = Recipe.objects.create(
            title="Weeknight dal", author=cls.owner, is_shared=True, servings=4
        )
        RecipeIngredient.objects.create(
            recipe=cls.recipe,
            ingredient=Ingredient.from_name("red lentils"),
            quantity=Decimal("250"),
            unit="g",
        )
        Step.objects.create(recipe=cls.recipe, position=0, text="Simmer.", minutes=25)

    def test_private_recipe_is_a_404_for_strangers(self):
        hidden = Recipe.objects.create(title="Hidden", author=self.owner, is_shared=False)
        response = self.client.get(hidden.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_servings_query_scales_the_ingredients(self):
        response = self.client.get(self.recipe.get_absolute_url(), {"servings": "2"})
        groups = response.context["ingredient_groups"]
        self.assertEqual(groups[0][1][0]["quantity"], "125")
        self.assertTrue(response.context["is_scaled"])

    def test_servings_are_clamped_to_something_sensible(self):
        response = self.client.get(self.recipe.get_absolute_url(), {"servings": "0"})
        self.assertEqual(response.context["servings"], 1)

        response = self.client.get(self.recipe.get_absolute_url(), {"servings": "99999"})
        self.assertEqual(response.context["servings"], 200)

    def test_nonsense_servings_fall_back_to_the_written_amount(self):
        response = self.client.get(self.recipe.get_absolute_url(), {"servings": "four"})
        self.assertEqual(response.context["servings"], 4)
        self.assertFalse(response.context["is_scaled"])

    def test_edit_controls_only_show_for_the_author(self):
        response = self.client.get(self.recipe.get_absolute_url())
        self.assertFalse(response.context["can_edit"])

        self.client.force_login(self.stranger)
        response = self.client.get(self.recipe.get_absolute_url())
        self.assertFalse(response.context["can_edit"])

        self.client.force_login(self.owner)
        response = self.client.get(self.recipe.get_absolute_url())
        self.assertTrue(response.context["can_edit"])


class EditingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user("owner", password="hunter2hunter2")
        cls.stranger = User.objects.create_user("stranger", password="hunter2hunter2")

    def payload(self, **overrides):
        data = {
            "title": "Weeknight dal",
            "summary": "Lentils, quickly.",
            "servings": "4",
            "serving_noun": "servings",
            "prep_minutes": "10",
            "cook_minutes": "30",
            "difficulty": "easy",
            "tags_text": "weeknight, Vegetarian, weeknight",
            "notes": "",
            "source_name": "",
            "source_url": "",
            "ingredients-TOTAL_FORMS": "2",
            "ingredients-INITIAL_FORMS": "0",
            "ingredients-MIN_NUM_FORMS": "0",
            "ingredients-MAX_NUM_FORMS": "1000",
            "ingredients-0-name": "red lentils",
            "ingredients-0-quantity": "250",
            "ingredients-0-unit": "g",
            "ingredients-0-preparation": "rinsed",
            "ingredients-0-group": "",
            "ingredients-1-name": "",
            "ingredients-1-quantity": "",
            "ingredients-1-unit": "",
            "ingredients-1-preparation": "",
            "ingredients-1-group": "",
            "steps-TOTAL_FORMS": "2",
            "steps-INITIAL_FORMS": "0",
            "steps-MIN_NUM_FORMS": "0",
            "steps-MAX_NUM_FORMS": "1000",
            "steps-0-text": "Simmer until collapsed.",
            "steps-0-minutes": "25",
            "steps-1-text": "",
            "steps-1-minutes": "",
        }
        data.update(overrides)
        return data

    def test_creating_a_recipe_requires_a_login(self):
        response = self.client.get(reverse("recipes:create"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_create_saves_ingredients_steps_and_tags(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("recipes:create"), self.payload())
        self.assertEqual(response.status_code, 302)

        recipe = Recipe.objects.get(title="Weeknight dal")
        self.assertEqual(recipe.author, self.owner)
        self.assertEqual(recipe.recipe_ingredients.count(), 1)
        self.assertEqual(recipe.steps.count(), 1)
        self.assertEqual(
            sorted(recipe.tags.values_list("name", flat=True)), ["vegetarian", "weeknight"]
        )

    def test_blank_formset_rows_are_ignored(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("recipes:create"), self.payload())
        recipe = Recipe.objects.get(title="Weeknight dal")
        self.assertEqual(recipe.recipe_ingredients.first().ingredient.name, "red lentils")

    def test_a_row_with_an_amount_but_no_ingredient_is_rejected(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("recipes:create"), self.payload(**{"ingredients-1-quantity": "3"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Recipe.objects.filter(title="Weeknight dal").exists())

    def test_positions_follow_the_order_the_rows_were_submitted_in(self):
        self.client.force_login(self.owner)
        self.client.post(
            reverse("recipes:create"),
            self.payload(
                **{
                    "steps-1-text": "Then serve.",
                    "steps-1-minutes": "",
                }
            ),
        )
        recipe = Recipe.objects.get(title="Weeknight dal")
        self.assertEqual(
            [(s.position, s.text) for s in recipe.steps.all()],
            [(0, "Simmer until collapsed."), (1, "Then serve.")],
        )

    def test_you_cannot_edit_someone_elses_recipe(self):
        recipe = Recipe.objects.create(title="Theirs", author=self.owner)
        self.client.force_login(self.stranger)
        response = self.client.get(reverse("recipes:edit", args=[recipe.slug]))
        self.assertEqual(response.status_code, 403)

    def test_you_cannot_delete_someone_elses_recipe(self):
        recipe = Recipe.objects.create(title="Theirs", author=self.owner)
        self.client.force_login(self.stranger)
        response = self.client.post(reverse("recipes:delete", args=[recipe.slug]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Recipe.objects.filter(pk=recipe.pk).exists())

    def test_mark_cooked_needs_a_post(self):
        recipe = Recipe.objects.create(title="Mine", author=self.owner, is_shared=True)
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.get(reverse("recipes:mark-cooked", args=[recipe.slug])).status_code,
            405,
        )
        self.client.post(reverse("recipes:mark-cooked", args=[recipe.slug]))
        recipe.refresh_from_db()
        self.assertEqual(recipe.times_cooked, 1)
