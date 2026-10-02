"""Theme profiles.

A profile is a plain dictionary in ``settings.CRUMBS_THEMES``. It needs five
things — a name, a description, an edge roundness, a primary colour and a
secondary colour — and everything else is worked out from those:

* the hover shade of the primary,
* lighter versions of both for dark mode, because a colour picked to sit on
  chalk white is usually too dark to read on a near-black page,
* black or white for text sitting *on* the primary, chosen by luminance, so a
  pale primary does not end up with white text on it.

Any derived value can be pinned in the profile instead. See the keys in
``DERIVED`` below.

Profiles come from two places, and a database profile wins over a settings one
with the same key:

* ``settings.CRUMBS_THEMES`` — shipped with the code, editable only by editing
  the file, and therefore safe from anyone with a browser;
* the ``Theme`` table — created and edited through the settings menu.

Nothing here trusts a profile blindly: colours are parsed and re-emitted as
hex, and roundness is matched against a pattern, because these values are
written into the ``<style>`` block at the top of ``base.html``. That block is
loaded after ``crumbs.css`` so it wins, and repeats itself inside the dark-mode
media query so it wins there too. Bad values in settings are reported by
``recipes/checks.py`` through ``manage.py check``; bad values from the settings
menu never get that far, because the form validates them first.
"""

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError

#: Used when a profile omits a key, and when settings define no themes at all.
FALLBACK = {
    "name": "Enamel",
    "description": "Chalk, slate and a jar of preserves.",
    "roundness": "3px",
    "primary": "#8e2c3f",
    "secondary": "#b6801c",
}

#: Keys that are computed from primary/secondary unless the profile sets them.
DERIVED = (
    "primary_hover",
    "primary_dark",
    "primary_dark_hover",
    "secondary_dark",
    "on_primary",
    "on_primary_dark",
)

ROUNDNESS = re.compile(r"^(0|\d{1,2}(\.\d+)?(px|rem|em))$")
HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

#: Dark text for use on a light accent. Not pure black — it sits next to --ink.
DARK_INK = "#15202a"


# --- Colour arithmetic ------------------------------------------------------


def parse_hex(value):
    """'#8e2c3f' or 'abc' -> (142, 44, 63). Returns None if it isn't a colour."""
    if not isinstance(value, str) or not HEX.match(value):
        return None
    digits = value.lstrip("#")
    if len(digits) == 3:
        digits = "".join(character * 2 for character in digits)
    return tuple(int(digits[i : i + 2], 16) for i in (0, 2, 4))


def to_hex(rgb):
    return "#%02x%02x%02x" % rgb


def mix(rgb, target, amount):
    """Move ``rgb`` a fraction of the way towards ``target``."""
    return tuple(round(c + (t - c) * amount) for c, t in zip(rgb, target))


def darken(value, amount):
    rgb = parse_hex(value)
    return to_hex(mix(rgb, BLACK, amount)) if rgb else value


def lighten(value, amount):
    rgb = parse_hex(value)
    return to_hex(mix(rgb, WHITE, amount)) if rgb else value


def relative_luminance(rgb):
    """WCAG relative luminance, 0 for black and 1 for white."""

    def channel(raw):
        c = raw / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(c) for c in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first, second):
    """WCAG contrast ratio between two colours, 1 to 21."""
    a, b = relative_luminance(first), relative_luminance(second)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def readable_on(value):
    """Dark ink or white, whichever actually contrasts better on ``value``.

    Measured rather than guessed at with a luminance threshold, because the
    crossover point sits lower than it looks — white on a mid pink reads at
    about 2.5:1, which is unusable.
    """
    rgb = parse_hex(value)
    if rgb is None:
        return "#ffffff"
    if contrast_ratio(rgb, parse_hex(DARK_INK)) >= contrast_ratio(rgb, WHITE):
        return DARK_INK
    return "#ffffff"


# --- Resolution -------------------------------------------------------------


def clean_roundness(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:g}px"
    if isinstance(value, str) and ROUNDNESS.match(value.strip()):
        return value.strip()
    return None


def clean_colour(value):
    rgb = parse_hex(value)
    return to_hex(rgb) if rgb else None


def validate_hex_colour(value):
    """Model/form validator. Blank passes; it means 'derive this one'."""
    if value and clean_colour(value) is None:
        raise ValidationError(
            "Enter a hex colour such as #8e2c3f.", code="invalid_colour"
        )


def validate_roundness(value):
    if value and clean_roundness(value) is None:
        raise ValidationError(
            "Enter a length such as 0, 6px or 0.4rem.", code="invalid_length"
        )


def builtin_profiles():
    """Profiles defined in settings.py. Not editable through the browser."""
    themes = getattr(settings, "CRUMBS_THEMES", None) or {}
    return {
        key: dict(profile, key=key, builtin=True)
        for key, profile in themes.items()
        if isinstance(profile, dict)
    }


def custom_profiles():
    """Profiles from the Theme table, or nothing if it isn't there yet."""
    from .models import Theme

    try:
        return {theme.key: theme.as_profile() for theme in Theme.objects.all()}
    except DatabaseError:
        # Before the first migrate, or a database that is briefly unavailable.
        # A missing theme is not a reason to fail a page.
        return {}


def available_themes():
    """Every profile, keyed. A stored theme shadows a built-in of the same key."""
    profiles = builtin_profiles()
    profiles.update(custom_profiles())
    return profiles


def active_key(request=None):
    """The key of the theme currently in use.

    The settings row wins; ``CRUMBS_THEME`` in settings.py is the fallback,
    which is what a fresh install uses before anyone opens the settings menu.
    """
    from .models import SiteSettings

    chosen = SiteSettings.load(request).theme
    return chosen or getattr(settings, "CRUMBS_THEME", "") or ""


def resolve_theme(key=None, request=None, themes=None):
    """Return the fully expanded theme profile for ``key``.

    Unknown keys, missing keys and unusable values all fall back rather than
    raising: a typo in a colour should leave the site readable, and
    ``manage.py check`` is where it gets reported.

    Pass ``themes`` to expand several profiles without re-reading the Theme
    table once per profile — that is what the settings gallery does.
    """
    if themes is None:
        themes = available_themes()
    if key is None:
        key = active_key(request)

    profile = themes.get(key)
    if not isinstance(profile, dict):
        profile = next(
            (value for value in themes.values() if isinstance(value, dict)), {}
        )

    theme = dict(FALLBACK)
    theme["key"] = key if key in themes else next(iter(themes), "")
    theme["builtin"] = bool(profile.get("builtin", True))

    for field in ("name", "description"):
        value = profile.get(field)
        if isinstance(value, str) and value.strip():
            theme[field] = value.strip()

    theme["roundness"] = clean_roundness(profile.get("roundness")) or FALLBACK["roundness"]
    theme["primary"] = clean_colour(profile.get("primary")) or FALLBACK["primary"]
    theme["secondary"] = clean_colour(profile.get("secondary")) or FALLBACK["secondary"]

    def pinned_or(field, default):
        theme[field] = clean_colour(profile.get(field)) or default
        return theme[field]

    pinned_or("primary_hover", darken(theme["primary"], 0.18))
    dark = pinned_or("primary_dark", lighten(theme["primary"], 0.45))
    # Derived from the resolved dark primary, not the raw one, so pinning
    # primary_dark also moves its hover shade and its text colour.
    pinned_or("primary_dark_hover", lighten(dark, 0.3))
    pinned_or("secondary_dark", lighten(theme["secondary"], 0.35))
    pinned_or("on_primary", readable_on(theme["primary"]))
    pinned_or("on_primary_dark", readable_on(dark))

    return theme


# --- Reference highlights ---------------------------------------------------


def reference_pair(foreground, background):
    """A readable highlight in both lighting modes.

    The theme states one pair, chosen against a pale page. On a near-black
    page that same pale wash is a glare, so the dark variant is derived: the
    wash is taken down to a deep tint of itself and the ink brought up, then
    the contrast is *measured* and overridden with plain black or white if
    the derived pair does not clear 4.5:1. Deriving and then checking beats
    deriving and hoping.
    """
    light_fg = clean_colour(foreground) or FALLBACK["primary"]
    light_bg = clean_colour(background) or "#ece3e6"

    if contrast_ratio(parse_hex(light_fg), parse_hex(light_bg)) < 4.5:
        light_fg = readable_on(light_bg)

    dark_bg = darken(light_bg, 0.78)
    dark_fg = lighten(light_fg, 0.72)
    if contrast_ratio(parse_hex(dark_fg), parse_hex(dark_bg)) < 4.5:
        dark_fg = readable_on(dark_bg)

    return {
        "light_fg": light_fg,
        "light_bg": light_bg,
        "dark_fg": dark_fg,
        "dark_bg": dark_bg,
    }
