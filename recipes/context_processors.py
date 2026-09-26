"""Context available to every template."""

from .appearance import choices, resolve_appearance
from .models import SiteSettings
from .theming import resolve_theme


def site(request):
    """Site identity, the classic colour profile, and the chosen appearance.

    ``theme`` is the classic profile, written into a <style> block in
    base.html; ``appearance`` is the full theme, applied as attributes on
    <html> and styled from the static themes.css. Both are resolved here so
    every page agrees on them, including error pages.
    """
    row = SiteSettings.load(request)
    return {
        "site": row,
        "theme": resolve_theme(request=request),
        "appearance": resolve_appearance(request, site=row),
        "appearance_options": choices(),
    }
