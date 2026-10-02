"""Template helpers for the panel.

The band maths lives here rather than in the view because it is presentation
— where to paint a rectangle — and it needs the whole row to work it out.
"""

from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()

#: The bar's right-hand edge, as a multiple of the target. A ceiling target
#: has to have room to be exceeded, or going over looks the same as hitting it.
HEADROOM = Decimal("1.25")


def _scale_for(row):
    """The value the right-hand end of the bar represents."""
    candidates = [row.get("upper"), row.get("lower"), row.get("value")]
    top = max((Decimal(str(c)) for c in candidates if c is not None), default=None)
    return top * HEADROOM if top else None


@register.simple_tag
def band_start(row):
    """Left edge of the acceptable band, as a percentage from the left."""
    scale = _scale_for(row)
    lower = row.get("lower")
    if not scale or lower is None:
        return 0
    return min(float(Decimal(str(lower)) / scale * 100), 100)


@register.simple_tag
def band_end(row):
    """Right *inset* of the band, as a percentage — this feeds `right:`.

    An "at least" target has no upper end, so its band runs to the edge.
    """
    scale = _scale_for(row)
    upper = row.get("upper")
    if not scale or upper is None:
        return 0
    return max(100 - float(Decimal(str(upper)) / scale * 100), 0)


@register.filter
def percentage(fraction):
    try:
        return min(max(float(fraction) * 100, 0), 100)
    except (TypeError, ValueError):
        return 0


@register.filter
def nutrient(entry, field):
    """One frozen nutrient off a log row, for display."""
    try:
        raw = entry.nutrients.get(field)
        return Decimal(str(raw)) if raw is not None else 0
    except (AttributeError, InvalidOperation, TypeError, ValueError):
        return 0
