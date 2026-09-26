"""Appearance: which theme, light or dark, and — for Woods — which season.

Two layers of theming now coexist, on purpose:

* the **classic** profiles in ``settings.CRUMBS_THEMES`` and the ``Theme``
  table, which recolour the one design by overriding a few custom properties;
* the six **full themes** below, which bring their own palette, typefaces and
  structural treatment, and are picked per browser rather than per site.

They coexist because they answer different questions. The classic profiles let
an administrator tune the house style for everyone; the full themes let each
reader choose a different house entirely. ``classic`` is one of the choices
here, which is how the two meet.

Everything here lives in a **cookie**, not the database: a theme is a property
of the screen you are looking at, and a household tablet propped in the
kitchen wants a different answer from the laptop it was added on. The cost is
that a second device starts from the default again.

Palettes are not in this file. They live in ``static/css/themes.css``, keyed
off ``data-theme`` and ``data-mode`` on ``<html>``, because a stylesheet gets
cached and an inline ``<style>`` block does not. Python's job is to pick a key,
prove it is one we know about, and put it on the element.
"""

import datetime

from django.conf import settings
from django.utils import timezone

COOKIE_THEME = "crumbs-theme"
COOKIE_MODE = "crumbs-mode"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365

#: How a theme shows a sticky note. See recipes/notes.py for what each means.
ICON, OVERLAY, SPLIT = "icon", "overlay", "split"

#: The paper a note's text is printed on.
STICKY, RULED, GRID = "sticky", "ruled", "grid"

THEMES = {
    "classic": {
        "name": "Classic",
        "description": (
            "The house style: chalk, slate and a jar of preserves. Recoloured "
            "site-wide from the settings menu."
        ),
        "note_mode": OVERLAY,
        "paper": RULED,
        "fonts": (
            "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600"
            "&family=Newsreader:opsz,wght@6..72,400;6..72,500;6..72,600&display=swap"
        ),
        "tunable": True,
    },
    "corporate": {
        "name": "Corporate",
        "description": (
            "Flat colour, square corners and ruled lines — the concrete "
            "jungle. The most legible of the six, and the only one with no "
            "shadows."
        ),
        "note_mode": SPLIT,
        "paper": GRID,
        "fonts": "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&display=swap",
    },
    "artsy": {
        "name": "Artsy",
        "description": (
            "Six shapes and six colours cycling together, so a button keeps "
            "the same identity wherever it appears. For inspiration."
        ),
        "note_mode": ICON,
        "paper": STICKY,
        "fonts": (
            "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@700"
            "&family=Work+Sans:wght@400;600&display=swap"
        ),
    },
    "cutesy": {
        "name": "Cutesy",
        "description": "Pink, white and soft-edged, with a diffuse shadow on everything.",
        "note_mode": ICON,
        "paper": STICKY,
        "fonts": "https://fonts.googleapis.com/css2?family=Quicksand:wght@500;700&display=swap",
    },
    "vaporwave": {
        "name": "Vaporwave",
        "description": (
            "Purple, blue and yellow. Neon outlines and glow in the dark; the "
            "same palette without the glow in the light, because neon on a "
            "pale ground is just thick borders."
        ),
        "note_mode": OVERLAY,
        "paper": GRID,
        "fonts": (
            "https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@700"
            "&family=IBM+Plex+Sans:wght@400;600&display=swap"
        ),
    },
    "woods": {
        "name": "Woods",
        "description": (
            "Bark down the left edge of every panel with the season's foliage "
            "at the top. The palette follows your calendar and hemisphere."
        ),
        "note_mode": ICON,
        "paper": RULED,
        "seasonal": True,
        "fonts": (
            "https://fonts.googleapis.com/css2?family=Alegreya:wght@700"
            "&family=Bitter:wght@400;600&display=swap"
        ),
    },
    "warm-retro": {
        "name": "Warm Retro",
        "description": "The colours, weight and edges of a kitchen from the 1970s.",
        "note_mode": OVERLAY,
        "paper": RULED,
        "fonts": (
            "https://fonts.googleapis.com/css2?family=Bree+Serif"
            "&family=Karla:wght@400;700&display=swap"
        ),
    },
}

DEFAULT_THEME = "classic"

MODES = ("auto", "light", "dark")
DEFAULT_MODE = "auto"

#: The toggle cycles rather than flips, so there is always a way back to
#: auto. Pinning light or dark with no return would mean never following the
#: browser's own setting again, which on a phone is often on a schedule.
NEXT_MODE = {"auto": "light", "light": "dark", "dark": "auto"}

#: Hours outside which the clock fallback calls it night. A fixed pair rather
#: than real solar times, which would need a latitude we deliberately don't
#: collect. In midwinter this is wrong by an hour or two; at high latitudes in
#: June it is wrong for most of the evening. It is only ever reached when the
#: browser reports no colour-scheme preference at all, which is rare.
DAY_STARTS, DAY_ENDS = 7, 19

SEASONS = ("winter", "spring", "summer", "autumn")

#: Enough of the IANA prefixes to place a reader below the equator. The
#: browser refines this client-side; this is only the server's first guess.
SOUTHERN_ZONES = (
    "Australia/",
    "Pacific/Auckland",
    "Pacific/Chatham",
    "Pacific/Fiji",
    "Pacific/Port_Moresby",
    "America/Argentina/",
    "America/Santiago",
    "America/Sao_Paulo",
    "America/Montevideo",
    "America/La_Paz",
    "America/Asuncion",
    "America/Lima",
    "Africa/Johannesburg",
    "Africa/Windhoek",
    "Africa/Harare",
    "Africa/Lusaka",
    "Africa/Maputo",
    "Africa/Nairobi",
    "Indian/",
    "Antarctica/",
)


def is_southern(zone_name):
    if not zone_name:
        return False
    return any(zone_name.startswith(prefix) for prefix in SOUTHERN_ZONES)


def season_for(month, southern=False):
    """Meteorological season for a month number, hemisphere-shifted.

    Calendar season, not climate: someone in Singapore gets a northern season
    that means nothing where they live. Fixing that properly needs a latitude.
    """
    northern = {12: "winter", 1: "winter", 2: "winter",
                3: "spring", 4: "spring", 5: "spring",
                6: "summer", 7: "summer", 8: "summer",
                9: "autumn", 10: "autumn", 11: "autumn"}[month]
    if not southern:
        return northern
    return {"winter": "summer", "spring": "autumn",
            "summer": "winter", "autumn": "spring"}[northern]


def clean_theme(value):
    """A cookie is user input. Only keys we know about get onto the element."""
    return value if value in THEMES else None


def clean_mode(value):
    return value if value in MODES else None


def resolve_appearance(request=None, site=None):
    """What to put on ``<html>`` for this request.

    ``mode`` is always a concrete light or dark, because the stylesheet should
    not have to reason about "auto". When the reader has chosen auto, the
    server guesses light and the pre-paint script in ``_appearance_boot.html``
    corrects it before anything is drawn. ``mode_pref`` keeps the reader's
    actual choice so the toggle can say so.
    """
    from .models import SiteSettings

    if site is None:
        site = SiteSettings.load(request)

    cookies = getattr(request, "COOKIES", {}) if request is not None else {}

    theme = clean_theme(cookies.get(COOKIE_THEME))
    if theme is None:
        theme = clean_theme(site.default_appearance) or DEFAULT_THEME

    mode_pref = clean_mode(cookies.get(COOKIE_MODE)) or DEFAULT_MODE
    mode = mode_pref if mode_pref in ("light", "dark") else "light"

    now = timezone.localtime() if timezone.is_aware(timezone.now()) else datetime.datetime.now()
    zone = getattr(timezone.get_current_timezone(), "key", str(timezone.get_current_timezone()))

    profile = THEMES[theme]
    return {
        "key": theme,
        "name": profile["name"],
        "description": profile["description"],
        "note_mode": profile["note_mode"],
        "paper": profile["paper"],
        "fonts": profile["fonts"],
        "tunable": profile.get("tunable", False),
        "seasonal": profile.get("seasonal", False),
        "mode": mode,
        "mode_pref": mode_pref,
        "next_mode": NEXT_MODE[mode_pref],
        "season": season_for(now.month, is_southern(zone)),
        "day_starts": DAY_STARTS,
        "day_ends": DAY_ENDS,
    }


def choices():
    """The picker's options, in a stable order with Classic first."""
    ordered = ["classic"] + [key for key in THEMES if key != "classic"]
    return [dict(THEMES[key], key=key) for key in ordered]


def cookie_kwargs():
    """Shared cookie settings. Not HttpOnly — the pre-paint script reads them."""
    return {
        "max_age": COOKIE_MAX_AGE,
        "samesite": "Lax",
        "secure": not settings.DEBUG,
        "path": "/",
    }
