from django.test import TestCase, override_settings
from django.urls import reverse

from recipes.appearance import (
    COOKIE_MODE,
    COOKIE_THEME,
    DEFAULT_THEME,
    THEMES,
    clean_mode,
    clean_theme,
    is_southern,
    season_for,
)
from recipes.models import SiteSettings


class RegistryTests(TestCase):
    def test_every_theme_declares_what_the_page_needs(self):
        for key, profile in THEMES.items():
            with self.subTest(theme=key):
                for field in ("name", "description", "note_mode", "paper", "fonts"):
                    self.assertTrue(profile.get(field), f"{key} is missing {field}")
                self.assertIn(profile["note_mode"], {"icon", "overlay", "split"})
                self.assertIn(profile["paper"], {"sticky", "ruled", "grid"})

    def test_all_six_plus_classic_are_present(self):
        self.assertEqual(
            set(THEMES),
            {"classic", "corporate", "artsy", "cutesy", "vaporwave", "woods", "warm-retro"},
        )

    def test_only_woods_is_seasonal(self):
        seasonal = {key for key, p in THEMES.items() if p.get("seasonal")}
        self.assertEqual(seasonal, {"woods"})

    def test_a_cookie_value_is_not_trusted(self):
        self.assertIsNone(clean_theme("'; DROP TABLE"))
        self.assertIsNone(clean_theme("../../etc/passwd"))
        self.assertEqual(clean_theme("woods"), "woods")
        self.assertIsNone(clean_mode("neon"))
        self.assertEqual(clean_mode("dark"), "dark")


class SeasonTests(TestCase):
    def test_northern_seasons(self):
        self.assertEqual(season_for(1), "winter")
        self.assertEqual(season_for(4), "spring")
        self.assertEqual(season_for(7), "summer")
        self.assertEqual(season_for(10), "autumn")

    def test_southern_seasons_are_shifted(self):
        self.assertEqual(season_for(1, southern=True), "summer")
        self.assertEqual(season_for(7, southern=True), "winter")

    def test_every_month_maps_somewhere(self):
        for month in range(1, 13):
            self.assertIn(season_for(month), {"winter", "spring", "summer", "autumn"})

    def test_hemisphere_from_timezone(self):
        self.assertTrue(is_southern("Australia/Perth"))
        self.assertTrue(is_southern("America/Argentina/Cordoba"))
        self.assertFalse(is_southern("Europe/London"))
        self.assertFalse(is_southern("America/Denver"))
        self.assertFalse(is_southern(""))


class RenderingTests(TestCase):
    def test_default_theme_when_no_cookie(self):
        response = self.client.get(reverse("recipes:list"))
        self.assertContains(response, f'data-theme="{DEFAULT_THEME}"')

    def test_the_cookie_chooses_the_theme(self):
        self.client.cookies[COOKIE_THEME] = "vaporwave"
        response = self.client.get(reverse("recipes:list"))
        self.assertContains(response, 'data-theme="vaporwave"')

    def test_a_junk_cookie_falls_back_rather_than_reaching_the_page(self):
        self.client.cookies[COOKIE_THEME] = '"><script>alert(1)</script>'
        response = self.client.get(reverse("recipes:list"))
        self.assertContains(response, f'data-theme="{DEFAULT_THEME}"')
        self.assertNotContains(response, "<script>alert(1)</script>")

    def test_the_site_default_applies_before_anyone_chooses(self):
        site = SiteSettings.load()
        site.default_appearance = "woods"
        site.save()
        response = self.client.get(reverse("recipes:list"))
        self.assertContains(response, 'data-theme="woods"')

    def test_a_reader_overrides_the_site_default(self):
        site = SiteSettings.load()
        site.default_appearance = "woods"
        site.save()
        self.client.cookies[COOKIE_THEME] = "cutesy"
        response = self.client.get(reverse("recipes:list"))
        self.assertContains(response, 'data-theme="cutesy"')

    def test_explicit_dark_reaches_the_markup(self):
        self.client.cookies[COOKIE_MODE] = "dark"
        response = self.client.get(reverse("recipes:list"))
        self.assertContains(response, 'data-mode="dark"')
        self.assertContains(response, 'data-mode-pref="dark"')

    def test_auto_renders_light_for_the_script_to_correct(self):
        response = self.client.get(reverse("recipes:list"))
        self.assertContains(response, 'data-mode="light"')
        self.assertContains(response, 'data-mode-pref="auto"')

    def test_only_woods_gets_a_season_attribute(self):
        self.client.cookies[COOKIE_THEME] = "woods"
        self.assertContains(self.client.get(reverse("recipes:list")), "data-season=")
        self.client.cookies[COOKIE_THEME] = "corporate"
        self.assertNotContains(self.client.get(reverse("recipes:list")), "data-season=")

    def test_the_classic_style_block_is_only_for_classic(self):
        response = self.client.get(reverse("recipes:list"))
        self.assertContains(response, ':root[data-theme="classic"]')

        self.client.cookies[COOKIE_THEME] = "artsy"
        response = self.client.get(reverse("recipes:list"))
        self.assertNotContains(response, ':root[data-theme="classic"]')

    def test_each_theme_loads_its_own_typefaces(self):
        self.client.cookies[COOKIE_THEME] = "warm-retro"
        self.assertContains(self.client.get(reverse("recipes:list")), "Bree+Serif")
        self.client.cookies[COOKIE_THEME] = "cutesy"
        self.assertContains(self.client.get(reverse("recipes:list")), "Quicksand")


class ChoosingTests(TestCase):
    def test_setting_a_theme_stores_a_cookie(self):
        response = self.client.post(reverse("recipes:set-theme"), {"theme": "inkwell"})
        self.assertNotIn(COOKIE_THEME, response.cookies)  # not one of the six

        response = self.client.post(reverse("recipes:set-theme"), {"theme": "woods"})
        self.assertEqual(response.cookies[COOKIE_THEME].value, "woods")

    def test_setting_a_mode_stores_a_cookie(self):
        response = self.client.post(reverse("recipes:set-mode"), {"mode": "dark"})
        self.assertEqual(response.cookies[COOKIE_MODE].value, "dark")

    def test_auto_is_a_real_choice_not_just_the_absence_of_one(self):
        response = self.client.post(reverse("recipes:set-mode"), {"mode": "auto"})
        self.assertEqual(response.cookies[COOKIE_MODE].value, "auto")

    def test_it_returns_you_to_the_page_you_were_on(self):
        response = self.client.post(
            reverse("recipes:set-theme"), {"theme": "artsy", "next": reverse("recipes:tags")}
        )
        self.assertEqual(response["Location"], reverse("recipes:tags"))

    def test_it_will_not_be_turned_into_an_open_redirect(self):
        response = self.client.post(
            reverse("recipes:set-theme"),
            {"theme": "artsy", "next": "https://example.invalid/phish"},
        )
        self.assertEqual(response["Location"], reverse("recipes:list"))

    def test_choosing_is_a_post(self):
        self.assertEqual(self.client.get(reverse("recipes:set-theme")).status_code, 405)

    @override_settings(DEBUG=False)
    def test_the_cookie_is_secure_outside_development(self):
        response = self.client.post(reverse("recipes:set-theme"), {"theme": "woods"})
        self.assertTrue(response.cookies[COOKIE_THEME]["secure"])
