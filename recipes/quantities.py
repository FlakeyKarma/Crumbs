"""Turning stored decimals back into numbers a cook would write down.

Quantities are stored as decimals because that is what arithmetic needs, but
"0.750" is not how anyone writes three quarters of a cup. Halving a recipe
should give you ¾, not 0.75, and certainly not 0.7500000001.
"""

from decimal import Decimal, InvalidOperation
from fractions import Fraction

#: Fractions that have a single character and are worth showing as one.
VULGAR = {
    Fraction(1, 8): "\u215b",
    Fraction(1, 6): "\u2159",
    Fraction(1, 5): "\u2155",
    Fraction(1, 4): "\u00bc",
    Fraction(1, 3): "\u2153",
    Fraction(3, 8): "\u215c",
    Fraction(2, 5): "\u2156",
    Fraction(1, 2): "\u00bd",
    Fraction(3, 5): "\u2157",
    Fraction(5, 8): "\u215d",
    Fraction(2, 3): "\u2154",
    Fraction(3, 4): "\u00be",
    Fraction(4, 5): "\u2158",
    Fraction(5, 6): "\u215a",
    Fraction(7, 8): "\u215e",
}

#: Denominators a kitchen scale or measuring spoon can actually honour.
KITCHEN_DENOMINATOR = 8

#: Below this, a quantity is a rounding artefact rather than an ingredient.
NEGLIGIBLE = Decimal("0.001")


def _trimmed_decimal(value):
    """'2.500' -> '2.5', '3.000' -> '3'."""
    quantised = value.quantize(Decimal("0.01"))
    text = format(quantised.normalize(), "f")
    return text


def format_quantity(value):
    """Render a decimal quantity the way a recipe card would.

    Returns an empty string for ``None`` so templates can simply print it
    next to an ingredient with no measurement ("salt, to taste").
    """
    if value is None:
        return ""
    try:
        value = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return ""

    if value < 0:
        return "-" + format_quantity(-value)
    if value < NEGLIGIBLE:
        return "0"

    # Large amounts are read as numbers, not fractions: 250 g, not 250 exactly.
    if value >= 10:
        return _trimmed_decimal(value)

    approximate = Fraction(value).limit_denominator(KITCHEN_DENOMINATOR)
    drift = abs(Decimal(approximate.numerator) / Decimal(approximate.denominator) - value)
    if drift > Decimal("0.02"):
        return _trimmed_decimal(value)

    whole = approximate.numerator // approximate.denominator
    remainder = approximate - whole

    if remainder == 0:
        return str(whole)

    symbol = VULGAR.get(remainder)
    if symbol is None:
        symbol = f"{remainder.numerator}/{remainder.denominator}"
        return f"{whole} {symbol}" if whole else symbol

    return f"{whole}{symbol}" if whole else symbol


def format_duration(minutes):
    """90 -> '1 hr 30 min'. Returns '' for nothing worth saying."""
    if not minutes:
        return ""
    minutes = int(minutes)
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours} hr {rest} min"
    if hours:
        return f"{hours} hr"
    return f"{rest} min"
