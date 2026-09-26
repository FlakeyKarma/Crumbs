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
    if "page" not in overrides:
        # Changing the filter or the sort should land you back on page one.
        params.pop("page", None)
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
