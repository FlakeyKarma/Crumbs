"""Template helpers for the pantry pages."""

from django import template
from django.http import QueryDict

register = template.Library()


@register.simple_tag(takes_context=True)
def page_url(context, param, number):
    """The current URL with one page parameter changed.

    Takes the parameter name as an argument because the search page paginates
    two lists — the catalogue and the open-source results — and turning the
    page on one must leave the other where it was. A `simple_tag` with keyword
    arguments cannot take a variable key, hence a positional one.
    """
    request = context.get("request")
    params = request.GET.copy() if request is not None else QueryDict(mutable=True)
    params[param] = number
    return f"?{params.urlencode()}"


@register.simple_tag(takes_context=True)
def size_url(context, size):
    """Change how many results a page shows, and go back to the first one.

    Both page numbers are dropped: page 4 of 25-at-a-time is not page 4 of
    100-at-a-time, and landing on an empty page after changing the size reads
    as a bug.
    """
    request = context.get("request")
    params = request.GET.copy() if request is not None else QueryDict(mutable=True)
    params["per_page"] = size
    params.pop("page", None)
    params.pop("xpage", None)
    return f"?{params.urlencode()}"
