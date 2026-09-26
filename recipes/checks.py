"""System checks for the theme settings.

Theme profiles are hand-edited dictionaries, so a typo is likely and its
symptom — one button quietly reverting to the fallback red — is easy to miss.
These run with ``manage.py check``, and therefore on every ``runserver``.
"""

from django.conf import settings
from django.core.checks import Error, Warning, register

from .theming import ROUNDNESS, clean_colour, clean_roundness

REQUIRED = ("name", "description", "roundness", "primary", "secondary")
COLOUR_FIELDS = (
    "primary",
    "secondary",
    "primary_hover",
    "primary_dark",
    "primary_dark_hover",
    "secondary_dark",
    "on_primary",
    "on_primary_dark",
)


@register()
def check_themes(app_configs, **kwargs):
    problems = []
    themes = getattr(settings, "CRUMBS_THEMES", None)

    if not themes:
        problems.append(
            Warning(
                "No theme profiles are defined.",
                hint="Add CRUMBS_THEMES to settings, or the built-in fallback is used.",
                id="recipes.W001",
            )
        )
        return problems

    if not isinstance(themes, dict):
        return [
            Error(
                "CRUMBS_THEMES must be a dict of profile key -> profile.",
                id="recipes.E001",
            )
        ]

    for key, profile in themes.items():
        label = f"CRUMBS_THEMES[{key!r}]"

        if not isinstance(profile, dict):
            problems.append(Error(f"{label} must be a dict.", id="recipes.E002"))
            continue

        for field in REQUIRED:
            if not profile.get(field):
                problems.append(
                    Error(f"{label} is missing {field!r}.", id="recipes.E003")
                )

        for field in COLOUR_FIELDS:
            value = profile.get(field)
            if value and clean_colour(value) is None:
                problems.append(
                    Error(
                        f"{label}[{field!r}] is not a hex colour: {value!r}.",
                        hint="Use three or six hex digits, for example '#8e2c3f'.",
                        id="recipes.E004",
                    )
                )

        roundness = profile.get("roundness")
        if roundness is not None and clean_roundness(roundness) is None:
            problems.append(
                Error(
                    f"{label}['roundness'] is not a usable length: {roundness!r}.",
                    hint=f"A number of pixels, or a string matching {ROUNDNESS.pattern}.",
                    id="recipes.E005",
                )
            )

    active = getattr(settings, "CRUMBS_THEME", "")
    if active and active not in themes:
        problems.append(
            Error(
                f"CRUMBS_THEME is {active!r}, which is not in CRUMBS_THEMES.",
                hint="One of: " + ", ".join(repr(k) for k in themes),
                id="recipes.E006",
            )
        )

    return problems
