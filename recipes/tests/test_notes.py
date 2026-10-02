from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from recipes.appearance import COOKIE_THEME
from recipes.forms import NoteForm
from recipes.models import Note, NoteShare, Recipe, Step


class NoteFactory:
    @staticmethod
    def make(recipe, author, **overrides):
        values = {"body": "Halve the sugar.", "visibility": Note.Visibility.PRIVATE}
        values.update(overrides)
        return Note.objects.create(recipe=recipe, author=author, **values)


class VisibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.cook = User.objects.create_user("cook", password="hunter2hunter2")
        cls.friend = User.objects.create_user("friend", password="hunter2hunter2")
        cls.stranger = User.objects.create_user("stranger", password="hunter2hunter2")
        cls.recipe = Recipe.objects.create(title="Dal", author=cls.cook, is_shared=True)

        cls.from_recipe = NoteFactory.make(
            cls.recipe, cls.cook, body="Use red lentils.", visibility=Note.Visibility.RECIPE
        )
        cls.private = NoteFactory.make(cls.recipe, cls.friend, body="Too salty last time.")
        cls.shared = NoteFactory.make(
            cls.recipe, cls.friend, body="Mum's version.", visibility=Note.Visibility.SHARED
        )
        NoteShare.objects.create(note=cls.shared, user=cls.stranger)

    def visible(self, user):
        return set(Note.objects.filter(recipe=self.recipe).visible_to(user))

    def test_anonymous_readers_get_only_the_recipe_notes(self):
        self.assertEqual(self.visible(AnonymousUser()), {self.from_recipe})

    def test_you_see_your_own_private_notes(self):
        self.assertEqual(
            self.visible(self.friend), {self.from_recipe, self.private, self.shared}
        )

    def test_a_shared_note_reaches_the_person_it_was_shared_with(self):
        self.assertEqual(self.visible(self.stranger), {self.from_recipe, self.shared})

    def test_the_recipe_owner_does_not_see_other_peoples_private_notes(self):
        self.assertEqual(self.visible(self.cook), {self.from_recipe})

    def test_readable_by_agrees_with_the_queryset(self):
        for user in (AnonymousUser(), self.cook, self.friend, self.stranger):
            with self.subTest(user=str(user)):
                expected = self.visible(user)
                for note in (self.from_recipe, self.private, self.shared):
                    self.assertEqual(note.readable_by(user), note in expected)

    def test_only_the_author_may_edit(self):
        self.assertTrue(self.private.editable_by(self.friend))
        self.assertFalse(self.private.editable_by(self.cook))
        self.assertFalse(self.private.editable_by(AnonymousUser()))

    def test_no_duplicate_shares(self):
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                NoteShare.objects.create(note=self.shared, user=self.stranger)


class ModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cook = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        cls.recipe = Recipe.objects.create(title="Dal", author=cls.cook)
        cls.other = Recipe.objects.create(title="Soup", author=cls.cook)

    def test_a_note_needs_something_in_it(self):
        note = Note(recipe=self.recipe, author=self.cook, body="   ")
        with self.assertRaises(ValidationError):
            note.clean()

    def test_an_anchor_must_belong_to_the_same_recipe(self):
        step = Step.objects.create(recipe=self.other, position=0, text="Stir.")
        note = Note(recipe=self.recipe, author=self.cook, body="Note", anchor_step=step)
        with self.assertRaises(ValidationError):
            note.clean()

    def test_placement_defaults_to_the_middle(self):
        note = NoteFactory.make(self.recipe, self.cook)
        self.assertEqual(int(note.offset_x), 50)
        self.assertEqual(int(note.offset_y), 50)
        self.assertEqual(note.rotation, 0)

    def test_summary_falls_back_to_the_picture(self):
        note = NoteFactory.make(self.recipe, self.cook, body="")
        self.assertEqual(note.summary, "Picture")

    def test_summary_is_trimmed(self):
        note = NoteFactory.make(self.recipe, self.cook, body="x" * 200)
        self.assertLessEqual(len(note.summary), 61)


class EditingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.cook = User.objects.create_user("cook", password="hunter2hunter2")
        cls.friend = User.objects.create_user("friend", password="hunter2hunter2")
        cls.recipe = Recipe.objects.create(title="Dal", author=cls.cook, is_shared=True)
        cls.step = Step.objects.create(recipe=cls.recipe, position=0, text="Simmer.")

    def payload(self, **overrides):
        data = {
            "body": "Halve the sugar.",
            "anchor_step": str(self.step.pk),
            "visibility": Note.Visibility.PRIVATE,
            "size": "m",
            "icon": "circle",
            "offset_x": "20",
            "offset_y": "80",
            "rotation": "181",
        }
        data.update(overrides)
        return data

    def test_adding_a_note_needs_an_account(self):
        response = self.client.post(
            reverse("recipes:note-create", args=[self.recipe.slug]), self.payload()
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_adding_a_note(self):
        self.client.force_login(self.friend)
        response = self.client.post(
            reverse("recipes:note-create", args=[self.recipe.slug]), self.payload()
        )
        self.assertEqual(response.status_code, 302)
        note = Note.objects.get()
        self.assertEqual(note.author, self.friend)
        self.assertEqual(note.recipe, self.recipe)
        self.assertEqual(note.anchor_step, self.step)
        self.assertEqual(note.rotation, 181)

    def test_upside_down_is_allowed(self):
        self.client.force_login(self.friend)
        self.client.post(
            reverse("recipes:note-create", args=[self.recipe.slug]),
            self.payload(rotation="180"),
        )
        self.assertEqual(Note.objects.get().rotation, 180)

    def test_a_rotation_off_the_dial_is_rejected(self):
        self.client.force_login(self.friend)
        response = self.client.post(
            reverse("recipes:note-create", args=[self.recipe.slug]),
            self.payload(rotation="400"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Note.objects.exists())

    def test_an_offset_outside_the_page_is_rejected(self):
        self.client.force_login(self.friend)
        response = self.client.post(
            reverse("recipes:note-create", args=[self.recipe.slug]),
            self.payload(offset_x="140"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Note.objects.exists())

    def test_an_empty_note_is_rejected(self):
        self.client.force_login(self.friend)
        response = self.client.post(
            reverse("recipes:note-create", args=[self.recipe.slug]), self.payload(body="  ")
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Note.objects.exists())

    def test_only_the_recipe_owner_may_write_a_recipe_note(self):
        form = NoteForm(recipe=self.recipe, author=self.friend)
        self.assertNotIn(
            Note.Visibility.RECIPE, [value for value, _ in form.fields["visibility"].choices]
        )

        form = NoteForm(recipe=self.recipe, author=self.cook)
        self.assertIn(
            Note.Visibility.RECIPE, [value for value, _ in form.fields["visibility"].choices]
        )

    def test_posting_a_recipe_note_as_someone_else_is_refused(self):
        self.client.force_login(self.friend)
        response = self.client.post(
            reverse("recipes:note-create", args=[self.recipe.slug]),
            self.payload(visibility=Note.Visibility.RECIPE),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Note.objects.exists())

    def test_you_cannot_edit_someone_elses_note(self):
        note = NoteFactory.make(self.recipe, self.cook)
        self.client.force_login(self.friend)
        self.assertEqual(
            self.client.get(reverse("recipes:note-edit", args=[note.pk])).status_code, 403
        )

    def test_you_cannot_delete_someone_elses_note(self):
        note = NoteFactory.make(self.recipe, self.cook)
        self.client.force_login(self.friend)
        response = self.client.post(reverse("recipes:note-delete", args=[note.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Note.objects.filter(pk=note.pk).exists())

    def test_deleting_your_own(self):
        note = NoteFactory.make(self.recipe, self.friend)
        self.client.force_login(self.friend)
        self.client.post(reverse("recipes:note-delete", args=[note.pk]))
        self.assertFalse(Note.objects.exists())

    def test_deleting_a_recipe_takes_its_notes(self):
        NoteFactory.make(self.recipe, self.friend)
        self.recipe.delete()
        self.assertFalse(Note.objects.exists())


class SharingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.cook = User.objects.create_user("cook", password="hunter2hunter2")
        cls.friend = User.objects.create_user("friend", password="hunter2hunter2")
        cls.recipe = Recipe.objects.create(title="Dal", author=cls.cook, is_shared=True)

    def setUp(self):
        self.note = NoteFactory.make(self.recipe, self.cook)
        self.client.force_login(self.cook)
        self.url = reverse("recipes:note-share", args=[self.note.pk])

    def test_sharing_flips_a_private_note_to_shared(self):
        self.client.post(self.url, {"username": "friend"})
        self.note.refresh_from_db()
        self.assertEqual(self.note.visibility, Note.Visibility.SHARED)
        self.assertTrue(self.note.shares.filter(user=self.friend).exists())

    def test_removing_the_last_share_makes_it_private_again(self):
        self.client.post(self.url, {"username": "friend"})
        self.client.post(self.url, {"remove": str(self.friend.pk)})
        self.note.refresh_from_db()
        self.assertEqual(self.note.visibility, Note.Visibility.PRIVATE)
        self.assertFalse(self.note.shares.exists())

    def test_sharing_twice_is_harmless(self):
        self.client.post(self.url, {"username": "friend"})
        self.client.post(self.url, {"username": "friend"})
        self.assertEqual(self.note.shares.count(), 1)

    def test_an_unknown_username_is_reported_not_swallowed(self):
        response = self.client.post(self.url, {"username": "nobody"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.note.shares.exists())

    def test_you_cannot_manage_sharing_on_someone_elses_note(self):
        self.client.force_login(self.friend)
        self.assertEqual(self.client.get(self.url).status_code, 403)


class RenderingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cook = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        cls.recipe = Recipe.objects.create(title="Dal", author=cls.cook, is_shared=True)
        cls.step = Step.objects.create(recipe=cls.recipe, position=0, text="Simmer.")
        cls.note = NoteFactory.make(
            cls.recipe,
            cls.cook,
            body="Use red lentils.",
            visibility=Note.Visibility.RECIPE,
            anchor_step=cls.step,
            rotation=200,
            icon="triangle",
        )

    def test_icon_themes_place_a_rotated_shape(self):
        self.client.cookies[COOKIE_THEME] = "artsy"
        response = self.client.get(self.recipe.get_absolute_url())
        self.assertContains(response, 'class="note-icon"')
        self.assertContains(response, 'data-shape="triangle"')
        self.assertContains(response, "--note-rotation: 200deg")

    def test_overlay_themes_use_tabs_instead(self):
        self.client.cookies[COOKIE_THEME] = "warm-retro"
        response = self.client.get(self.recipe.get_absolute_url())
        self.assertContains(response, 'class="note-tab"')
        self.assertNotContains(response, 'class="note-icon"')

    def test_split_themes_collect_everything_in_one_panel(self):
        self.client.cookies[COOKIE_THEME] = "corporate"
        response = self.client.get(self.recipe.get_absolute_url())
        self.assertContains(response, 'class="note-split"')
        self.assertNotContains(response, 'class="note-icon"')
        self.assertNotContains(response, 'class="note-tab"')

    def test_the_paper_follows_the_theme(self):
        self.client.cookies[COOKIE_THEME] = "cutesy"
        self.assertContains(self.client.get(self.recipe.get_absolute_url()), 'data-paper="sticky"')
        self.client.cookies[COOKIE_THEME] = "corporate"
        self.assertContains(self.client.get(self.recipe.get_absolute_url()), 'data-paper="grid"')

    def test_placement_survives_a_change_of_theme(self):
        for theme in ("artsy", "corporate", "warm-retro"):
            self.client.cookies[COOKIE_THEME] = theme
            self.client.get(self.recipe.get_absolute_url())
        self.note.refresh_from_db()
        self.assertEqual(self.note.rotation, 200)
        self.assertEqual(self.note.icon, "triangle")

    def test_a_private_note_does_not_render_for_a_stranger(self):
        NoteFactory.make(self.recipe, self.cook, body="Secret tweak.")
        response = self.client.get(self.recipe.get_absolute_url())
        self.assertNotContains(response, "Secret tweak.")

    def test_the_cook_session_token_is_stable_within_a_session(self):
        first = self.client.get(self.recipe.get_absolute_url()).context["cook_session"]
        second = self.client.get(self.recipe.get_absolute_url()).context["cook_session"]
        self.assertEqual(first, second)
        self.assertTrue(first)


class PlacementTests(TestCase):
    """Dragging a note, and turning it with the arrows."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.cook = User.objects.create_user("cook", password="hunter2hunter2")
        cls.friend = User.objects.create_user("friend", password="hunter2hunter2")
        cls.recipe = Recipe.objects.create(title="Dal", author=cls.cook, is_shared=True)

    def setUp(self):
        self.note = NoteFactory.make(self.recipe, self.cook)
        self.url = reverse("recipes:note-place", args=[self.note.pk])
        self.client.force_login(self.cook)

    def place(self, **data):
        return self.client.post(self.url, data)

    def test_a_new_note_lands_in_placement_mode(self):
        step = Step.objects.create(recipe=self.recipe, position=0, text="Simmer.")
        response = self.client.post(
            reverse("recipes:note-create", args=[self.recipe.slug]),
            {
                "body": "Halve the sugar.",
                "anchor_step": str(step.pk),
                "visibility": Note.Visibility.PRIVATE,
                "size": "m", "icon": "circle", "colour": "tomato",
                "offset_x": "50", "offset_y": "50", "rotation": "0",
            },
        )
        note = Note.objects.latest("pk")
        self.assertEqual(response["Location"], f"{self.recipe.get_absolute_url()}?place={note.pk}")

    def test_the_toolbar_appears_for_the_note_being_placed(self):
        response = self.client.get(f"{self.recipe.get_absolute_url()}?place={self.note.pk}")
        self.assertEqual(response.context["placing_note"], self.note)
        self.assertContains(response, "data-placing-note")

    def test_no_toolbar_without_the_parameter(self):
        response = self.client.get(self.recipe.get_absolute_url())
        self.assertIsNone(response.context["placing_note"])

    def test_you_cannot_place_someone_elses_note(self):
        self.client.force_login(self.friend)
        response = self.client.get(f"{self.recipe.get_absolute_url()}?place={self.note.pk}")
        self.assertIsNone(response.context["placing_note"])
        self.assertEqual(self.place(offset_x="10", offset_y="10").status_code, 403)

    def test_dragging_saves_the_position(self):
        self.place(offset_x="12.5", offset_y="87.25", rotation="45")
        self.note.refresh_from_db()
        self.assertEqual(self.note.offset_x, Decimal("12.50"))
        self.assertEqual(self.note.offset_y, Decimal("87.25"))
        self.assertEqual(self.note.rotation, 45)

    def test_a_position_off_the_anchor_is_pulled_back_on(self):
        self.place(offset_x="-40", offset_y="250")
        self.note.refresh_from_db()
        self.assertEqual(self.note.offset_x, Decimal("0.00"))
        self.assertEqual(self.note.offset_y, Decimal("100.00"))

    def test_rotation_wraps_instead_of_sticking(self):
        """Turning right past 359 comes back to 0 — an upside-down note is a
        feature, so the dial must go all the way round."""
        for sent, expected in (("370", 10), ("360", 0), ("-15", 345), ("720", 0)):
            with self.subTest(sent=sent):
                self.place(rotation=sent)
                self.note.refresh_from_db()
                self.assertEqual(self.note.rotation, expected)

    def test_rubbish_is_ignored_rather_than_zeroing_the_note(self):
        self.place(offset_x="30", offset_y="30", rotation="90")
        self.place(offset_x="banana", rotation="sideways")
        self.note.refresh_from_db()
        self.assertEqual(self.note.offset_x, Decimal("30.00"))
        self.assertEqual(self.note.rotation, 90)

    def test_placing_is_a_post(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_it_answers_json_to_the_script(self):
        response = self.client.post(
            self.url,
            {"offset_x": "20", "offset_y": "40", "rotation": "15"},
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rotation"], 15)

    def test_it_redirects_for_a_plain_form_post(self):
        response = self.place(offset_x="20", offset_y="40")
        self.assertEqual(response.status_code, 302)


class ColourTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cook = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        cls.recipe = Recipe.objects.create(title="Dal", author=cls.cook, is_shared=True)

    def test_a_note_defaults_to_the_theme_accent(self):
        self.assertEqual(NoteFactory.make(self.recipe, self.cook).colour, "accent")

    def test_the_colour_reaches_the_icon_and_the_paper(self):
        NoteFactory.make(
            self.recipe, self.cook, colour="basil", visibility=Note.Visibility.RECIPE
        )
        self.client.cookies[COOKIE_THEME] = "artsy"
        response = self.client.get(self.recipe.get_absolute_url())
        self.assertContains(response, 'data-colour="basil"')

    def test_only_offered_colours_are_accepted(self):
        self.client.force_login(self.cook)
        response = self.client.post(
            reverse("recipes:note-create", args=[self.recipe.slug]),
            {
                "body": "x", "visibility": Note.Visibility.PRIVATE,
                "size": "m", "icon": "circle", "colour": "#ff0000",
                "offset_x": "50", "offset_y": "50", "rotation": "0",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Note.objects.exists())


class DialogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cook = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        cls.recipe = Recipe.objects.create(title="Dal", author=cls.cook, is_shared=True)

    def test_the_dialog_is_on_the_page_for_someone_who_can_write(self):
        self.client.force_login(self.cook)
        response = self.client.get(self.recipe.get_absolute_url())
        self.assertContains(response, 'id="note-dialog"')
        self.assertContains(response, "data-note-dialog-open")

    def test_the_button_still_links_to_the_full_form(self):
        """With no JavaScript the dialog never opens, so the link has to work."""
        self.client.force_login(self.cook)
        response = self.client.get(self.recipe.get_absolute_url())
        self.assertContains(response, reverse("recipes:note-create", args=[self.recipe.slug]))

    def test_no_dialog_for_a_signed_out_reader(self):
        response = self.client.get(self.recipe.get_absolute_url())
        self.assertNotContains(response, 'id="note-dialog"')
