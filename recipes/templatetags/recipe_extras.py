from django import template
from django.http import QueryDict

from ..quantities import format_duration

register = template.Library()


@register.filter(name="duration")
def duration(minutes):
    """90 -> '1 hr 30 min'."""
    return format_duration(minutes)


@register.simple_tag(takes_context=True)
def query_with(context, **overrides):
    """Rebuild the current query string with a few parameters changed.

    Lets a sort link keep the active search term and tag filter instead of
    silently dropping them: ``{% query_with sort="title" %}``.
    """
    request = context.get("request")
    params = QueryDict(mutable=True)
    if request is not None:
        params = request.GET.copy()
    for key, value in overrides.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    # Changing a filter or a sort should land you back on page one — of
    # every list on the page, since a page with two lists has two page
    # parameters and neither survives a change of filter.
    if not any(key.endswith("page") for key in overrides):
        for key in [k for k in params if k.endswith("page")]:
            params.pop(key, None)
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"


@register.filter(name="get_item")
def get_item(mapping, key):
    """Look a dict up by a variable key.

    Django templates resolve ``{{ d.key }}`` as a literal string key, so a
    dict bucketed by primary key needs this. Returns an empty list rather
    than nothing, so the caller can loop over it either way.
    """
    if not hasattr(mapping, "get"):
        return []
    return mapping.get(key) or []


@register.simple_tag
def with_references(text, bindings=None, library=None):
    """Render prose, turning `**filler**` into a reference button.

    A tag rather than a filter because it needs the recipe (to find the
    reference) and the reader's bindings (to show the food they chose).
    Escaping happens inside — see recipes/references.py.
    """
    from ..references import render

    return render(text, bindings=bindings, references=library)
