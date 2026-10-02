"""Context available to every template."""

from .appearance import choices, resolve_appearance
from .models import SiteSettings, UserAppearance
from .theming import reference_pair, resolve_theme


def site(request):
    """Site identity, the classic colour profile, and the chosen appearance.

    ``theme`` is the classic profile, written into a <style> block in
    base.html; ``appearance`` is the full theme, applied as attributes on
    <html> and styled from the static themes.css. Both are resolved here so
    every page agrees on them, including error pages.
    """
    row = SiteSettings.load(request)
    appearance = resolve_appearance(request, site=row)

    # Reader first, then the theme. Someone who cannot read the highlight
    # should fix it once, not per device, so this override lives on the
    # account rather than in the theme cookie.
    mine = UserAppearance.for_user(getattr(request, "user", None))
    highlight = reference_pair(
        (mine.reference_foreground if mine else "") or appearance["reference_fg"],
        (mine.reference_background if mine else "") or appearance["reference_bg"],
    )

    return {
        "site": row,
        "theme": resolve_theme(request=request),
        "appearance": appearance,
        "appearance_options": choices(),
        "highlight": highlight,
    }
