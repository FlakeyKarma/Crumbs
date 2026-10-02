"""References: a pattern library in the pantry, matched against recipe prose."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from nutrition.models import Food, MealFood, Unit
from recipes import references
from recipes.models import (
    FoodReference,
    Recipe,
    Reference,
    ReferencePattern,
    UserFoodReference,
)


class Fixtures(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cook = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        cls.unit = Unit.objects.create(name="gram", symbol="g", dimension="mass")

    def portion(self, name, percent="100"):
        food = Food.objects.create(name=name, reference_unit=self.unit)
        return MealFood.objects.create(
            source_food=food, portion_percentage=Decimal(percent)
        )

    def reference(self, *patterns, label="", regex=False):
        """A term plus its phrasings, which are rows now rather than a field."""
        references.forget()
        term = Reference.objects.create(label=label or patterns[0])
        for position, pattern in enumerate(patterns):
            ReferencePattern.objects.create(
                reference=term, pattern=pattern, is_regex=regex, position=position
            )
        return term


class PatternTests(Fixtures):
    def setUp(self):
        references.forget()

    def test_plain_words_are_a_valid_pattern(self):
        self.assertTrue(references.compile_pattern("lean beef"))

    def test_several_plain_phrasings_cover_one_term(self):
        """The point of the list: three rows, no regular expression needed."""
        reference = self.reference(
            "lean beef", "lean minced beef", "beef mince", label="Lean beef"
        )
        for phrase in ("lean beef", "lean minced beef", "beef mince", "LEAN BEEF"):
            with self.subTest(phrase=phrase):
                self.assertTrue(reference.matches(phrase))
        self.assertFalse(reference.matches("pork mince"))

    def test_a_regex_row_still_works_when_opted_in(self):
        reference = self.reference(
            r"lean\s+(minced\s+)?beef", label="Lean beef", regex=True
        )
        self.assertTrue(reference.matches("lean minced beef"))

    def test_plain_rows_are_escaped_so_punctuation_is_literal(self):
        """A non-regex row saying 'beef (lean)' should match those characters."""
        reference = self.reference("beef (lean)", label="Lean beef")
        self.assertTrue(reference.matches("beef (lean)"))
        self.assertFalse(reference.matches("beef lean"))

    def test_a_term_with_no_phrasings_matches_nothing(self):
        bare = Reference.objects.create(label="Orphan")
        self.assertFalse(bare.matches("orphan"))

    def test_matching_covers_the_whole_phrase(self):
        """A row of 'beef' must not quietly claim 'beef stock'."""
        reference = self.reference("beef")
        self.assertTrue(reference.matches("beef"))
        self.assertFalse(reference.matches("beef stock"))
        self.assertFalse(reference.matches("corned beef"))

    def test_someone_who_wants_substrings_asks_for_them(self):
        self.assertTrue(
            self.reference(".*beef.*", regex=True).matches("corned beef")
        )

    def test_an_invalid_regex_is_refused_with_a_reason(self):
        with self.assertRaises(ValidationError):
            ReferencePattern(pattern="[unclosed", is_regex=True).clean()

    def test_the_same_text_as_plain_words_is_fine(self):
        """Escaped, so somebody who is not writing a regex cannot write a bad one."""
        ReferencePattern(pattern="[unclosed", is_regex=False).clean()

    def test_a_regex_that_could_hang_is_refused(self):
        """Nested quantifiers backtrack catastrophically and `re` has no timeout."""
        for danger in ("(a+)+b", "(x*)*", "(ab+)+c"):
            with self.subTest(pattern=danger):
                with self.assertRaises(ValidationError):
                    ReferencePattern(pattern=danger, is_regex=True).clean()

    def test_an_absurdly_long_pattern_is_refused(self):
        with self.assertRaises(ValidationError):
            ReferencePattern(pattern="a" * 250).clean()

    def test_an_empty_pattern_is_refused(self):
        with self.assertRaises(ValidationError):
            ReferencePattern(pattern="   ").clean()

    def test_a_term_is_unique(self):
        self.reference("lean beef", label="Lean beef")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Reference.objects.create(label="Lean beef")

    def test_a_phrasing_is_not_repeated_within_a_term(self):
        term = self.reference("lean beef")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ReferencePattern.objects.create(reference=term, pattern="lean beef")

    def test_removing_a_phrasing_stops_it_matching(self):
        reference = self.reference("beef", "pork")
        self.assertTrue(reference.matches("beef"))
        reference.patterns.filter(pattern="beef").delete()
        references.forget()
        self.assertFalse(reference.matches("beef"))
        self.assertTrue(reference.matches("pork"))


class MatchingTests(Fixtures):
    def setUp(self):
        references.forget()
        self.beef = self.reference("lean beef", "beef mince", label="Lean beef")
        self.onion = self.reference(
            r"(yellow |brown )?onions?", label="Onion", regex=True
        )

    def test_the_library_recognises_a_phrase(self):
        self.assertEqual(references.match("beef mince"), self.beef)
        self.assertEqual(references.match("yellow onion"), self.onion)

    def test_an_unknown_phrase_matches_nothing(self):
        self.assertIsNone(references.match("galangal"))

    def test_spelling_is_normalised_before_matching(self):
        self.assertEqual(references.match("  Lean   Beef "), self.beef)

    def test_collisions_are_reported_not_hidden(self):
        """First match wins, so two patterns claiming one phrase is worth saying."""
        self.reference(".*beef.*", label="Anything beefy", regex=True)
        clashes = references.collisions()
        self.assertTrue(clashes)
        self.assertIn("beef", clashes[0]["phrase"])

    def test_extraction_finds_the_phrases_in_order(self):
        found = references.extract("Brown the **lean beef**, add **onion**.")
        self.assertEqual(found, ["lean beef", "onion"])

    def test_an_unclosed_marker_is_not_a_phrase(self):
        self.assertEqual(references.extract("**beef and then prose"), [])

    def test_a_phrase_cannot_span_a_line(self):
        self.assertEqual(references.extract("**beef\nand onion**"), [])


class RenderTests(Fixtures):
    def setUp(self):
        references.forget()
        self.beef = self.reference("lean beef", "beef mince", label="Lean beef")

    def test_a_recognised_phrase_becomes_a_button(self):
        html = references.render("Brown the **lean beef**.")
        self.assertIn('class="reference"', html)
        self.assertIn('data-known="true"', html)
        self.assertNotIn("**", html)

    def test_an_unrecognised_phrase_is_still_a_button(self):
        """It opens the picker, which offers to create the reference."""
        html = references.render("Add the **galangal**.")
        self.assertIn('data-known="false"', html)
        self.assertIn('data-reference=""', html)

    def test_surrounding_prose_is_escaped(self):
        html = references.render("<script>bad()</script> **lean beef**")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_a_phrase_containing_markup_is_escaped(self):
        html = references.render("**<b>beef</b>**")
        self.assertNotIn("<b>", html)

    def test_a_bound_reference_shows_the_portion(self):
        portion = self.portion("Beef mince 5%")
        html = references.render(
            "Brown the **lean beef**.", bindings={self.beef.pk: portion}
        )
        self.assertIn("Beef mince 5%", html)
        self.assertIn('data-bound="true"', html)


class FoodReferenceTests(Fixtures):
    def setUp(self):
        references.forget()
        self.beef = self.reference("lean beef", label="Lean beef")

    def test_a_reference_carries_a_list_of_portions(self):
        for name in ("Beef mince 5%", "Beef mince 20%", "Braising steak"):
            FoodReference.objects.create(reference=self.beef, meal_food=self.portion(name))
        self.assertEqual(self.beef.food_references.count(), 3)

    def test_a_portion_appears_once_per_reference(self):
        portion = self.portion("Beef mince 5%")
        FoodReference.objects.create(reference=self.beef, meal_food=portion)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FoodReference.objects.create(reference=self.beef, meal_food=portion)

    def test_the_options_endpoint_feeds_the_dropdown(self):
        FoodReference.objects.create(
            reference=self.beef, meal_food=self.portion("Beef mince 5%", "50")
        )
        self.client.force_login(self.cook)
        data = self.client.get(
            reverse("pantry:reference-options", args=[self.beef.pk])
        ).json()
        self.assertEqual(data["results"][0]["name"], "Beef mince 5%")
        self.assertIn("50", data["results"][0]["description"])

    def test_attaching_and_detaching_through_the_pantry(self):
        portion = self.portion("Beef mince 5%")
        self.client.force_login(self.cook)
        url = reverse("pantry:reference-attach", args=[self.beef.pk])

        self.client.post(url, {"meal_food": portion.pk})
        self.assertEqual(self.beef.food_references.count(), 1)

        self.client.post(url, {"detach": portion.pk})
        self.assertEqual(self.beef.food_references.count(), 0)


class BindingTests(Fixtures):
    def setUp(self):
        references.forget()
        self.friend = get_user_model().objects.create_user(
            "friend", password="hunter2hunter2"
        )
        self.recipe = Recipe.objects.create(
            title="Chilli", author=self.cook, is_shared=True,
            summary="Brown the **lean beef**.",
        )
        self.beef = self.reference("lean beef", label="Lean beef")
        self.lean = self.portion("Beef mince 5%")
        self.fatty = self.portion("Beef mince 20%")
        self.url = reverse("recipes:bind-reference", args=[self.beef.pk])

    def bind(self, portion, recipe=None):
        return self.client.post(
            self.url,
            {"meal_food": portion.pk, "recipe": (recipe or self.recipe).slug},
        )

    def test_binding_records_user_recipe_reference_and_portion(self):
        self.client.force_login(self.cook)
        self.assertEqual(self.bind(self.lean).status_code, 200)
        row = UserFoodReference.objects.get()
        self.assertEqual(
            (row.user, row.recipe, row.reference, row.meal_food),
            (self.cook, self.recipe, self.beef, self.lean),
        )

    def test_two_readers_can_mean_different_things(self):
        self.client.force_login(self.cook)
        self.bind(self.lean)
        self.client.force_login(self.friend)
        self.bind(self.fatty)
        self.assertEqual(UserFoodReference.objects.count(), 2)

    def test_the_same_reference_can_differ_between_recipes(self):
        other = Recipe.objects.create(
            title="Stroganoff", author=self.cook, is_shared=True,
            summary="Sear the **lean beef**.",
        )
        self.client.force_login(self.cook)
        self.bind(self.lean)
        self.bind(self.fatty, recipe=other)
        self.assertEqual(UserFoodReference.objects.count(), 2)

    def test_choosing_again_replaces(self):
        self.client.force_login(self.cook)
        self.bind(self.lean)
        self.bind(self.fatty)
        self.assertEqual(UserFoodReference.objects.count(), 1)
        self.assertEqual(UserFoodReference.objects.get().meal_food, self.fatty)

    def test_a_choice_can_be_cleared(self):
        self.client.force_login(self.cook)
        self.bind(self.lean)
        self.client.post(self.url, {"clear": "1", "recipe": self.recipe.slug})
        self.assertFalse(UserFoodReference.objects.exists())

    def test_a_portion_off_the_shortlist_is_still_allowed(self):
        """The list is a shortcut, not a fence."""
        self.client.force_login(self.cook)
        self.assertEqual(self.beef.food_references.count(), 0)
        self.assertEqual(self.bind(self.lean).status_code, 200)

    def test_binding_needs_an_account(self):
        self.assertEqual(self.bind(self.lean).status_code, 302)

    def test_you_cannot_bind_against_a_recipe_you_cannot_see(self):
        hidden = Recipe.objects.create(title="Hidden", author=self.cook, is_shared=False)
        self.client.force_login(self.friend)
        self.assertEqual(self.bind(self.lean, recipe=hidden).status_code, 404)

    def test_the_page_shows_this_reader_their_own_choice(self):
        self.client.force_login(self.cook)
        self.bind(self.lean)
        self.assertContains(self.client.get(self.recipe.get_absolute_url()), "Beef mince 5%")
        self.client.force_login(self.friend)
        response = self.client.get(self.recipe.get_absolute_url())
        self.assertContains(response, "lean beef")
        self.assertNotContains(response, "Beef mince 5%")


class CreateFromPhraseTests(Fixtures):
    def setUp(self):
        references.forget()
        self.client.force_login(self.cook)

    def test_a_phrase_can_become_a_reference_without_leaving_the_recipe(self):
        response = self.client.post(
            reverse("recipes:create-reference"), {"phrase": "galangal"}
        )
        self.assertTrue(response.json()["created"])
        self.assertTrue(Reference.objects.filter(label="galangal").exists())

    def test_the_phrase_is_escaped_into_the_pattern(self):
        """A phrase from a recipe is words, not somebody's regex."""
        self.client.post(reverse("recipes:create-reference"), {"phrase": "beef (lean)"})
        reference = Reference.objects.get()
        self.assertTrue(reference.matches("beef (lean)"))
        self.assertFalse(reference.matches("beef lean"))

    def test_asking_twice_does_not_duplicate(self):
        self.client.post(reverse("recipes:create-reference"), {"phrase": "galangal"})
        response = self.client.post(
            reverse("recipes:create-reference"), {"phrase": "galangal"}
        )
        self.assertFalse(response.json()["created"])
        self.assertEqual(Reference.objects.count(), 1)

    def test_an_empty_phrase_is_refused(self):
        response = self.client.post(reverse("recipes:create-reference"), {"phrase": "  "})
        self.assertEqual(response.status_code, 400)


class PantryTabTests(Fixtures):
    def setUp(self):
        references.forget()
        self.client.force_login(self.cook)

    def test_the_references_page_is_a_pantry_subtab(self):
        self.assertContains(
            self.client.get(reverse("pantry:home")), reverse("pantry:references")
        )

    def test_the_page_lists_the_library(self):
        self.reference("lean beef", label="Lean beef")
        self.assertContains(self.client.get(reverse("pantry:references")), "Lean beef")

    def test_a_term_can_be_added_from_the_page(self):
        self.client.post(reverse("pantry:references"), {"label": "Onion"})
        self.assertTrue(Reference.objects.filter(label="Onion").exists())

    def test_the_page_needs_an_account(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("pantry:references")).status_code, 302)


class SearchTests(Fixtures):
    def setUp(self):
        references.forget()
        self.client.force_login(self.cook)
        self.beef = self.reference("lean beef", "beef mince", label="Lean beef")
        self.onion = self.reference("onion", label="Onion")

    def page(self, **params):
        return self.client.get(reverse("pantry:references"), params)

    def test_searching_by_the_term(self):
        found = self.page(q="beef").context["library"]
        self.assertEqual([r.pk for r in found], [self.beef.pk])

    def test_searching_by_a_phrasing_finds_its_term(self):
        """Looking up 'mince' should find the term called 'Lean beef'."""
        found = self.page(q="mince").context["library"]
        self.assertEqual([r.pk for r in found], [self.beef.pk])

    def test_search_ignores_case(self):
        self.assertEqual(len(self.page(q="BEEF").context["library"]), 1)

    def test_a_term_matching_on_two_phrasings_appears_once(self):
        self.reference("beef", "beef bits", label="Beefy")
        found = self.page(q="beef").context["library"]
        self.assertEqual(len(found), len(set(r.pk for r in found)))

    def test_no_search_shows_everything(self):
        self.assertEqual(len(self.page().context["library"]), 2)

    def test_nothing_matching_says_so(self):
        self.assertContains(self.page(q="zzzz"), "Nothing matches")

    def test_the_search_box_keeps_what_was_typed(self):
        self.assertContains(self.page(q="beef"), 'value="beef"')


class AddPhrasingTests(Fixtures):
    """The "+" flow: open the panel, type, Save."""

    def setUp(self):
        references.forget()
        self.client.force_login(self.cook)
        self.beef = self.reference("lean beef", label="Lean beef")
        self.url = reverse("pantry:reference-pattern-add", args=[self.beef.pk])

    def test_adding_a_plain_phrasing(self):
        self.client.post(self.url, {"pattern": "beef mince"})
        self.assertEqual(self.beef.patterns.count(), 2)
        references.forget()
        self.assertTrue(self.beef.matches("beef mince"))

    def test_a_new_phrasing_goes_on_the_end(self):
        self.client.post(self.url, {"pattern": "beef mince"})
        self.assertEqual(
            list(self.beef.patterns.values_list("pattern", flat=True)),
            ["lean beef", "beef mince"],
        )

    def test_a_phrasing_can_opt_in_to_regex(self):
        self.client.post(self.url, {"pattern": r"beef\s+mince", "is_regex": "on"})
        self.assertTrue(self.beef.patterns.get(pattern=r"beef\s+mince").is_regex)

    def test_a_bad_regex_is_refused_and_nothing_is_added(self):
        self.client.post(self.url, {"pattern": "(a+)+b", "is_regex": "on"})
        self.assertEqual(self.beef.patterns.count(), 1)

    def test_the_same_bad_text_as_plain_words_is_accepted(self):
        self.client.post(self.url, {"pattern": "(a+)+b"})
        self.assertEqual(self.beef.patterns.count(), 2)

    def test_a_duplicate_phrasing_is_refused(self):
        self.client.post(self.url, {"pattern": "lean beef"})
        self.assertEqual(self.beef.patterns.count(), 1)

    def test_editing_the_text(self):
        row = self.beef.patterns.get()
        self.client.post(
            reverse("pantry:reference-pattern-edit", args=[row.pk]),
            {"pattern": "beef mince"},
        )
        row.refresh_from_db()
        self.assertEqual(row.pattern, "beef mince")

    def test_the_edit_takes_effect_immediately(self):
        """The compiled cache must be dropped, or the old text keeps matching
        until the process restarts — which looks like the save failed."""
        row = self.beef.patterns.get()
        self.assertTrue(self.beef.matches("lean beef"))
        self.client.post(
            reverse("pantry:reference-pattern-edit", args=[row.pk]),
            {"pattern": "beef mince"},
        )
        self.beef.refresh_from_db()
        self.assertFalse(self.beef.matches("lean beef"))
        self.assertTrue(self.beef.matches("beef mince"))

    def test_turning_the_regex_box_on(self):
        row = self.beef.patterns.get()
        self.client.post(
            reverse("pantry:reference-pattern-edit", args=[row.pk]),
            {"pattern": r"lean\s+beef", "is_regex": "on"},
        )
        row.refresh_from_db()
        self.assertTrue(row.is_regex)
        self.assertTrue(self.beef.matches("lean    beef"))

    def test_turning_the_regex_box_off_makes_the_text_literal(self):
        row = self.beef.patterns.get()
        self.client.post(
            reverse("pantry:reference-pattern-edit", args=[row.pk]),
            {"pattern": "beef (lean)"},
        )
        row.refresh_from_db()
        self.assertFalse(row.is_regex)
        self.assertTrue(self.beef.matches("beef (lean)"))
        self.assertFalse(self.beef.matches("beef lean"))

    def test_editing_into_a_bad_regex_is_refused(self):
        row = self.beef.patterns.get()
        self.client.post(
            reverse("pantry:reference-pattern-edit", args=[row.pk]),
            {"pattern": "(a+)+b", "is_regex": "on"},
        )
        row.refresh_from_db()
        self.assertEqual(row.pattern, "lean beef")

    def test_editing_into_a_duplicate_is_refused(self):
        self.client.post(self.url, {"pattern": "beef mince"})
        row = self.beef.patterns.get(pattern="beef mince")
        self.client.post(
            reverse("pantry:reference-pattern-edit", args=[row.pk]),
            {"pattern": "lean beef"},
        )
        row.refresh_from_db()
        self.assertEqual(row.pattern, "beef mince")

    def test_editing_to_nothing_is_refused(self):
        row = self.beef.patterns.get()
        self.client.post(
            reverse("pantry:reference-pattern-edit", args=[row.pk]), {"pattern": "  "}
        )
        row.refresh_from_db()
        self.assertEqual(row.pattern, "lean beef")

    def test_editing_comes_back_to_the_reference(self):
        row = self.beef.patterns.get()
        response = self.client.post(
            reverse("pantry:reference-pattern-edit", args=[row.pk]),
            {"pattern": "beef mince"},
        )
        self.assertIn(f"#reference-{self.beef.pk}", response["Location"])

    def test_editing_needs_an_account_and_a_post(self):
        row = self.beef.patterns.get()
        url = reverse("pantry:reference-pattern-edit", args=[row.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(url, {"pattern": "x"}).status_code, 302)

    def test_the_edit_panel_is_on_the_page_with_the_current_values(self):
        self.client.post(self.url, {"pattern": r"beef\s+mince", "is_regex": "on"})
        body = self.client.get(reverse("pantry:references")).content.decode()
        self.assertIn("reference-pattern-edit", body)
        self.assertIn('name="is_regex" checked', body.replace(" >", ">"))

    def test_removing_a_phrasing(self):
        row = self.beef.patterns.get()
        self.client.post(reverse("pantry:reference-pattern-remove", args=[row.pk]))
        self.assertEqual(self.beef.patterns.count(), 0)

    def test_removing_the_last_one_warns_that_nothing_will_match(self):
        from django.contrib.messages import get_messages

        row = self.beef.patterns.get()
        response = self.client.post(
            reverse("pantry:reference-pattern-remove", args=[row.pk])
        )
        self.assertIn(
            "nothing will match",
            " ".join(str(m) for m in get_messages(response.wsgi_request)),
        )

    def test_it_comes_back_to_the_reference_it_edited(self):
        response = self.client.post(self.url, {"pattern": "beef mince"})
        self.assertIn(f"#reference-{self.beef.pk}", response["Location"])

    def test_adding_needs_an_account_and_a_post(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(self.url, {"pattern": "x"}).status_code, 302)


class PortionPickerTests(Fixtures):
    """Every reference can take portions, and the list is searchable."""

    def setUp(self):
        references.forget()
        self.client.force_login(self.cook)
        self.beef = self.reference("lean beef", label="Lean beef")

    def test_every_reference_offers_the_control(self):
        self.reference("onion", label="Onion")
        response = self.client.get(reverse("pantry:references"))
        self.assertEqual(response.content.decode().count("data-attach-open"), 2)

    def test_the_portion_search_needs_two_letters(self):
        self.assertEqual(
            self.client.get(reverse("pantry:meal-food-search"), {"q": "b"}).json()[
                "results"
            ],
            [],
        )

    def test_it_finds_a_portion_by_its_food(self):
        self.portion("Beef mince 5%")
        results = self.client.get(
            reverse("pantry:meal-food-search"), {"q": "beef"}
        ).json()["results"]
        self.assertEqual(results[0]["name"], "Beef mince 5%")
        self.assertIn("portion", results[0]["description"])

    def test_the_search_needs_an_account(self):
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse("pantry:meal-food-search"), {"q": "beef"}).status_code,
            302,
        )

    def test_attaching_from_the_picker_uses_the_ordinary_form(self):
        portion = self.portion("Beef mince 5%")
        self.client.post(
            reverse("pantry:reference-attach", args=[self.beef.pk]),
            {"meal_food": portion.pk},
        )
        self.assertEqual(self.beef.food_references.count(), 1)


class ComputedPortionTests(Fixtures):
    """The portion comes from the recipe line, not from the attachment."""

    def setUp(self):
        from django.core.management import call_command

        references.forget()
        call_command("seed_nutrition", verbosity=0)
        self.gram = Unit.objects.get(symbol="g")
        self.client.force_login(self.cook)

        self.beef = self.reference("lean beef", label="Lean beef")
        self.food = Food.objects.create(name="Beef mince 5%", reference_unit=self.gram)
        from nutrition.models import CoreMacro, Macro

        Macro.objects.create(
            food=self.food,
            core_macro=CoreMacro.objects.get(slug="energy"),
            component_name="Energy",
            unit=Unit.objects.get(symbol="kcal"),
            unit_count=Decimal("176"),
        )
        self.meal_food = MealFood.objects.create(source_food=self.food)

    def recipe_with(self, quantity, unit, name="lean beef"):
        from recipes.models import Ingredient, RecipeIngredient

        recipe = Recipe.objects.create(
            title="Chilli", author=self.cook, is_shared=True,
            summary="Brown the **lean beef**.",
        )
        RecipeIngredient.objects.create(
            recipe=recipe,
            ingredient=Ingredient.from_name(name),
            quantity=Decimal(quantity) if quantity else None,
            unit=unit,
        )
        return recipe

    def bind(self, recipe):
        from recipes.models import UserFoodReference

        return UserFoodReference.objects.create(
            user=self.cook, recipe=recipe, reference=self.beef, meal_food=self.meal_food
        )

    def test_the_amount_comes_from_the_recipe_line(self):
        recipe = self.recipe_with("500", "g")
        self.bind(recipe)
        row = references.portions_for(self.cook, recipe)[0]
        self.assertIsNone(row["problem"])
        self.assertEqual(row["amount"], Decimal("500"))
        self.assertEqual(row["energy"], Decimal("880"))

    def test_the_same_choice_in_a_different_recipe_is_a_different_portion(self):
        """Which is why it is not stored on the reference."""
        big = self.recipe_with("500", "g")
        self.bind(big)
        small = self.recipe_with("200", "g")
        self.bind(small)
        self.assertEqual(references.portions_for(self.cook, big)[0]["amount"], Decimal("500"))
        self.assertEqual(references.portions_for(self.cook, small)[0]["amount"], Decimal("200"))

    def test_the_ingredient_line_is_found_by_the_same_patterns(self):
        """One set of phrasings recognises the prose and the ingredient row."""
        recipe = self.recipe_with("500", "g", name="beef mince")
        self.bind(recipe)
        self.assertIn(
            "no ingredient line", references.portions_for(self.cook, recipe)[0]["problem"]
        )

        self.beef.patterns.create(pattern="beef mince")
        references.forget()
        self.assertIsNone(references.portions_for(self.cook, recipe)[0]["problem"])

    def test_a_line_in_a_unit_that_cannot_convert_reports_the_gap(self):
        recipe = self.recipe_with("2", "tbsp")
        self.bind(recipe)
        row = references.portions_for(self.cook, recipe)[0]
        self.assertIsNone(row["amount"])
        self.assertIn("density", row["problem"])

    def test_no_matching_line_is_reported_not_guessed(self):
        recipe = self.recipe_with("500", "g", name="onion")
        self.bind(recipe)
        self.assertIn(
            "no ingredient line", references.portions_for(self.cook, recipe)[0]["problem"]
        )

    def test_choosing_a_meal_has_nothing_to_scale_by_weight(self):
        from nutrition.models import Meal

        meal = Meal.objects.create(name="Bolognese base")
        portion = MealFood.objects.create(source_is_meal=True, source_meal=meal)
        recipe = self.recipe_with("500", "g")
        from recipes.models import UserFoodReference

        UserFoodReference.objects.create(
            user=self.cook, recipe=recipe, reference=self.beef, meal_food=portion
        )
        self.assertIn("meal", references.portions_for(self.cook, recipe)[0]["problem"])

    def test_signed_out_readers_get_nothing(self):
        from django.contrib.auth.models import AnonymousUser

        recipe = self.recipe_with("500", "g")
        self.assertEqual(references.portions_for(AnonymousUser(), recipe), [])

    def test_the_recipe_page_shows_the_worked_out_portion(self):
        recipe = self.recipe_with("500", "g")
        self.bind(recipe)
        response = self.client.get(recipe.get_absolute_url())
        self.assertContains(response, "Your choices")
        self.assertContains(response, "Beef mince 5%")
