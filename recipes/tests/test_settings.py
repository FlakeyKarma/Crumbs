from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from recipes.models import Recipe, SiteSettings, Theme


def make_theme(**overrides):
    values = {
        "key": "midnight",
        "name": "Midnight",
        "description": "Blue and quiet.",
        "roundness": "8px",
        "primary": "#24406b",
        "secondary": "#8a6410",
    }
    values.update(overrides)
    return Theme.objects.create(**values)


class SiteSettingsModelTests(TestCase):
    def test_load_creates_the_row_once(self):
        first = SiteSettings.load()
        second = SiteSettings.load()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(SiteSettings.objects.count(), 1)

    def test_saving_a_second_instance_overwrites_the_first(self):
        SiteSettings.load()
        stray = SiteSettings(site_name="Second")
        stray.save()
        self.assertEqual(SiteSettings.objects.count(), 1)
        self.assertEqual(SiteSettings.load().site_name, "Second")

    def test_it_cannot_be_deleted(self):
        with self.assertRaises(NotImplementedError):
            SiteSettings.load().delete()

    def test_load_memoises_on_the_request_when_given_one(self):
        class FakeRequest:
            pass

        request = FakeRequest()
        first = SiteSettings.load(request)
        SiteSettings.objects.filter(pk=first.pk).update(site_name="Changed")
        self.assertIs(SiteSettings.load(request), first)
        self.assertEqual(SiteSettings.load().site_name, "Changed")


class AccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.cook = User.objects.create_user("cook", password="hunter2hunter2")
        cls.admin = User.objects.create_user(
            "admin", password="hunter2hunter2", is_staff=True
        )

    def test_signed_out_visitors_are_sent_to_the_login_page(self):
        response = self.client.get(reverse("recipes:settings"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_a_signed_in_non_admin_is_refused_rather_than_bounced(self):
        self.client.force_login(self.cook)
        response = self.client.get(reverse("recipes:settings"))
        self.assertEqual(response.status_code, 403)

    def test_staff_get_in(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("recipes:settings")).status_code, 200)

    def test_every_settings_url_is_protected(self):
        make_theme()
        self.client.force_login(self.cook)
        for name, args in [
            ("recipes:settings", []),
            ("recipes:theme-create", []),
            ("recipes:theme-edit", ["midnight"]),
            ("recipes:theme-delete", ["midnight"]),
        ]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403)
        self.assertEqual(
            self.client.post(reverse("recipes:theme-activate", args=["midnight"])).status_code,
            403,
        )

    def test_the_nav_only_offers_settings_to_staff(self):
        self.client.force_login(self.cook)
        self.assertNotContains(
            self.client.get(reverse("recipes:list")), reverse("recipes:settings")
        )
        self.client.force_login(self.admin)
        self.assertContains(
            self.client.get(reverse("recipes:list")), reverse("recipes:settings")
        )


class SiteSettingsViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_user(
            "admin", password="hunter2hunter2", is_staff=True
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def payload(self, **overrides):
        data = {
            "site_name": "The Recipe Box",
            "tagline": "Everything we actually cook.",
            "recipes_per_page": "20",
            "default_servings": "4",
        }
        data.update(overrides)
        return data

    def test_saving_changes_the_stored_row(self):
        response = self.client.post(reverse("recipes:settings"), self.payload())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SiteSettings.load().site_name, "The Recipe Box")

    def test_the_site_name_reaches_every_page(self):
        self.client.post(reverse("recipes:settings"), self.payload())
        self.assertContains(self.client.get(reverse("recipes:list")), "The Recipe Box")

    def test_an_out_of_range_page_size_is_rejected(self):
        response = self.client.post(
            reverse("recipes:settings"), self.payload(recipes_per_page="900")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SiteSettings.load().recipes_per_page, 20)

    def test_page_size_is_honoured_by_the_index(self):
        for number in range(8):
            Recipe.objects.create(
                title=f"Recipe {number}", author=self.admin, is_shared=True
            )
        self.client.post(reverse("recipes:settings"), self.payload(recipes_per_page="5"))
        response = self.client.get(reverse("recipes:list"))
        self.assertEqual(len(response.context["recipes"]), 5)

    def test_defaults_reach_the_new_recipe_form(self):
        self.client.post(
            reverse("recipes:settings"),
            self.payload(default_servings="6", share_new_recipes="on"),
        )
        response = self.client.get(reverse("recipes:create"))
        form = response.context["form"]
        self.assertEqual(form.initial["servings"], 6)
        self.assertTrue(form.initial["is_shared"])

    def test_turning_off_anonymous_browsing_closes_the_public_pages(self):
        recipe = Recipe.objects.create(title="Dal", author=self.admin, is_shared=True)
        self.client.post(reverse("recipes:settings"), self.payload())  # unticked
        self.client.logout()

        for url in [reverse("recipes:list"), reverse("recipes:tags"), recipe.get_absolute_url()]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response["Location"])

    def test_anonymous_browsing_is_on_by_default(self):
        Recipe.objects.create(title="Dal", author=self.admin, is_shared=True)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("recipes:list")).status_code, 200)


class ThemeManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_user(
            "admin", password="hunter2hunter2", is_staff=True
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def payload(self, **overrides):
        data = {
            "key": "midnight",
            "name": "Midnight",
            "description": "Blue and quiet.",
            "roundness": "8px",
            "primary": "#24406b",
            "secondary": "#8a6410",
            "primary_hover": "",
            "primary_dark": "",
            "primary_dark_hover": "",
            "secondary_dark": "",
            "on_primary": "",
            "on_primary_dark": "",
        }
        data.update(overrides)
        return data

    def test_the_gallery_lists_the_built_in_themes(self):
        response = self.client.get(reverse("recipes:settings"))
        keys = [entry["key"] for entry in response.context["themes"]]
        self.assertIn("enamel", keys)
        self.assertIn("inkwell", keys)

    def test_creating_a_theme(self):
        response = self.client.post(reverse("recipes:theme-create"), self.payload())
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Theme.objects.filter(key="midnight").exists())

    def test_a_bad_colour_is_rejected_by_the_form(self):
        response = self.client.post(
            reverse("recipes:theme-create"), self.payload(primary="octarine")
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Theme.objects.exists())
        self.assertIn("primary", response.context["form"].errors)

    def test_a_bad_roundness_is_rejected_by_the_form(self):
        response = self.client.post(
            reverse("recipes:theme-create"), self.payload(roundness="}{ body{display:none")
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Theme.objects.exists())

    def test_copying_a_builtin_prefills_the_form_and_leaves_the_original(self):
        response = self.client.get(reverse("recipes:theme-create"), {"from": "inkwell"})
        initial = response.context["form"].initial
        self.assertEqual(initial["primary"], "#1f3a5f")
        self.assertEqual(initial["key"], "inkwell-copy")
        self.assertFalse(Theme.objects.exists())

    def test_activating_a_theme_changes_what_the_pages_render(self):
        make_theme()
        self.client.post(reverse("recipes:theme-activate", args=["midnight"]))
        self.assertEqual(SiteSettings.load().theme, "midnight")
        response = self.client.get(reverse("recipes:list"))
        self.assertContains(response, "--jam: #24406b")
        self.assertContains(response, "--radius: 8px")

    def test_activating_an_unknown_theme_is_a_404(self):
        response = self.client.post(reverse("recipes:theme-activate", args=["nope"]))
        self.assertEqual(response.status_code, 404)

    def test_a_stored_theme_shadows_a_builtin_with_the_same_key(self):
        make_theme(key="enamel", name="My Enamel", primary="#004d40")
        self.client.post(reverse("recipes:theme-activate", args=["enamel"]))
        response = self.client.get(reverse("recipes:list"))
        self.assertContains(response, "--jam: #004d40")

    def test_editing_a_theme_cannot_change_its_key(self):
        make_theme()
        self.client.post(
            reverse("recipes:theme-edit", args=["midnight"]),
            self.payload(key="somethingelse", name="Renamed"),
        )
        theme = Theme.objects.get()
        self.assertEqual(theme.key, "midnight")
        self.assertEqual(theme.name, "Renamed")

    def test_deleting_the_active_theme_falls_back(self):
        make_theme()
        self.client.post(reverse("recipes:theme-activate", args=["midnight"]))
        self.client.post(reverse("recipes:theme-delete", args=["midnight"]))
        self.assertFalse(Theme.objects.exists())
        self.assertEqual(SiteSettings.load().theme, "")
        self.assertEqual(self.client.get(reverse("recipes:list")).status_code, 200)

    def test_deleting_a_shadowing_theme_uncovers_the_builtin(self):
        make_theme(key="enamel", primary="#004d40")
        self.client.post(reverse("recipes:theme-activate", args=["enamel"]))
        self.client.post(reverse("recipes:theme-delete", args=["enamel"]))
        self.assertEqual(SiteSettings.load().theme, "enamel")
        response = self.client.get(reverse("recipes:list"))
        self.assertContains(response, "--jam: #8e2c3f")

    def test_overrides_are_stored_and_used(self):
        self.client.post(
            reverse("recipes:theme-create"), self.payload(primary_dark="#a9c4ff")
        )
        self.client.post(reverse("recipes:theme-activate", args=["midnight"]))
        response = self.client.get(reverse("recipes:list"))
        self.assertContains(response, "--jam: #a9c4ff")

    def test_a_blank_override_is_derived_instead(self):
        self.client.post(reverse("recipes:theme-create"), self.payload())
        profile = Theme.objects.get().as_profile()
        self.assertNotIn("primary_dark", profile)
