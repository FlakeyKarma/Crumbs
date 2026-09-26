from django.test import SimpleTestCase, TestCase, override_settings

from recipes.checks import check_themes
from recipes.theming import (
    FALLBACK,
    clean_roundness,
    darken,
    lighten,
    readable_on,
    resolve_theme,
)

THEMES = {
    "enamel": {
        "name": "Enamel",
        "description": "The default.",
        "roundness": "3px",
        "primary": "#8e2c3f",
        "secondary": "#b6801c",
        "primary_dark": "#e0899a",
    },
    "orchard": {
        "name": "Orchard",
        "description": "Green and soft.",
        "roundness": 10,
        "primary": "#2f6b4f",
        "secondary": "#c2791f",
    },
}


class ColourTests(SimpleTestCase):
    def test_darken_and_lighten_move_in_the_right_direction(self):
        self.assertEqual(darken("#808080", 0.5), "#404040")
        self.assertEqual(lighten("#808080", 0.5), "#c0c0c0")

    def test_short_hex_is_expanded(self):
        self.assertEqual(darken("#fff", 0), "#ffffff")

    def test_white_text_on_a_dark_accent(self):
        self.assertEqual(readable_on("#8e2c3f"), "#ffffff")

    def test_dark_text_on_a_pale_accent(self):
        self.assertEqual(readable_on("#f6d76b"), "#15202a")
        # The shipped dark-mode pink: white on this reads at about 2.5:1.
        self.assertEqual(readable_on("#e0899a"), "#15202a")

    def test_nonsense_colours_are_left_alone_rather_than_crashing(self):
        self.assertEqual(darken("not a colour", 0.5), "not a colour")
        self.assertEqual(readable_on("not a colour"), "#ffffff")


class RoundnessTests(SimpleTestCase):
    def test_numbers_become_pixels(self):
        self.assertEqual(clean_roundness(10), "10px")
        self.assertEqual(clean_roundness(0), "0px")

    def test_usable_strings_pass_through(self):
        self.assertEqual(clean_roundness("0"), "0")
        self.assertEqual(clean_roundness(" 0.4rem "), "0.4rem")

    def test_anything_else_is_rejected(self):
        self.assertIsNone(clean_roundness("12"))
        self.assertIsNone(clean_roundness("calc(1px + 2px)"))
        self.assertIsNone(clean_roundness("red; } body { display: none"))
        self.assertIsNone(clean_roundness(True))


@override_settings(CRUMBS_THEMES=THEMES, CRUMBS_THEME="orchard")
class ResolveThemeTests(TestCase):
    """A TestCase, not a SimpleTestCase: resolving a theme now reads the
    Theme table and the site settings row as well as settings.py."""

    def test_the_named_profile_is_used(self):
        theme = resolve_theme()
        self.assertEqual(theme["name"], "Orchard")
        self.assertEqual(theme["primary"], "#2f6b4f")
        self.assertEqual(theme["roundness"], "10px")

    def test_derived_values_are_filled_in(self):
        theme = resolve_theme("orchard")
        self.assertEqual(theme["primary_hover"], darken("#2f6b4f", 0.18))
        self.assertEqual(theme["primary_dark"], lighten("#2f6b4f", 0.45))
        self.assertEqual(theme["on_primary"], "#ffffff")

    def test_a_pinned_value_wins_over_the_derived_one(self):
        self.assertEqual(resolve_theme("enamel")["primary_dark"], "#e0899a")

    def test_every_key_the_template_uses_is_present(self):
        theme = resolve_theme()
        for key in (
            "roundness",
            "primary",
            "primary_hover",
            "primary_dark",
            "primary_dark_hover",
            "secondary",
            "secondary_dark",
            "on_primary",
            "on_primary_dark",
        ):
            self.assertTrue(theme[key], key)

    def test_an_unknown_name_falls_back_to_the_first_profile(self):
        with self.settings(CRUMBS_THEME="nope"):
            self.assertEqual(resolve_theme()["name"], "Enamel")

    def test_no_themes_at_all_still_renders(self):
        with self.settings(CRUMBS_THEMES={}, CRUMBS_THEME=""):
            theme = resolve_theme()
            self.assertEqual(theme["primary"], FALLBACK["primary"])
            self.assertEqual(theme["roundness"], FALLBACK["roundness"])

    def test_a_broken_value_falls_back_instead_of_reaching_the_page(self):
        broken = {"bad": dict(THEMES["orchard"], primary="octarine", roundness="}{")}
        with self.settings(CRUMBS_THEMES=broken, CRUMBS_THEME="bad"):
            theme = resolve_theme()
            self.assertEqual(theme["primary"], FALLBACK["primary"])
            self.assertEqual(theme["roundness"], FALLBACK["roundness"])


class ThemeCheckTests(SimpleTestCase):
    def ids(self, **settings_kwargs):
        with self.settings(**settings_kwargs):
            return sorted(problem.id for problem in check_themes(None))

    def test_a_good_configuration_is_quiet(self):
        self.assertEqual(self.ids(CRUMBS_THEMES=THEMES, CRUMBS_THEME="enamel"), [])

    def test_missing_themes_warns(self):
        self.assertEqual(self.ids(CRUMBS_THEMES={}, CRUMBS_THEME=""), ["recipes.W001"])

    def test_a_missing_required_key_is_reported(self):
        broken = {"x": {"name": "X", "roundness": "3px", "primary": "#000", "secondary": "#fff"}}
        self.assertIn("recipes.E003", self.ids(CRUMBS_THEMES=broken, CRUMBS_THEME="x"))

    def test_a_bad_colour_is_reported(self):
        broken = {"x": dict(THEMES["orchard"], primary="octarine")}
        self.assertIn("recipes.E004", self.ids(CRUMBS_THEMES=broken, CRUMBS_THEME="x"))

    def test_a_bad_roundness_is_reported(self):
        broken = {"x": dict(THEMES["orchard"], roundness="}{")}
        self.assertIn("recipes.E005", self.ids(CRUMBS_THEMES=broken, CRUMBS_THEME="x"))

    def test_an_unknown_active_theme_is_reported(self):
        self.assertIn(
            "recipes.E006", self.ids(CRUMBS_THEMES=THEMES, CRUMBS_THEME="missing")
        )


class ThemeInPageTests(TestCase):
    @override_settings(CRUMBS_THEMES=THEMES, CRUMBS_THEME="orchard")
    def test_the_active_theme_reaches_the_stylesheet_block(self):
        response = self.client.get("/")
        self.assertContains(response, "--jam: #2f6b4f")
        self.assertContains(response, "--radius: 10px")
