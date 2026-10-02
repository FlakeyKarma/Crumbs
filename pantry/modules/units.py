"""Getting a recipe line to grams.

Nutrition is stored per 100 g, so scaling a nutrient is trivial arithmetic:
``value * grams / 100``. Everything difficult happens before that, in turning
"2 cloves of garlic" or "1 cup of flour" into a number of grams.

Three kinds of unit, three kinds of answer:

* **mass** (g, kg, oz, lb) — exact, no food knowledge needed;
* **volume** (ml, l, tsp, tbsp, cup, fl oz) — needs the food's density, and a
  cup of flour and a cup of honey are not close;
* **count** (clove, slice, can, package, sprig, pinch) — needs a stated weight
  for that food's unit, which is what ``FoodPortion`` rows are for.

When we can't get there, the answer is ``None`` and the caller says so. There
is deliberately no "assume 1 ml = 1 g" fallback: a silently wrong calorie
count is worse than a visible gap, and the fix is data — a portion row — not
a guess.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

#: Grams per unit, for units that measure mass.
MASS_TO_GRAMS = {
    "g": Decimal("1"),
    "kg": Decimal("1000"),
    "oz": Decimal("28.349523125"),
    "lb": Decimal("453.59237"),
}

#: Millilitres per unit, for units that measure volume. US customary, because
#: that is what the recipe sources Crumbs imports from use.
VOLUME_TO_ML = {
    "ml": Decimal("1"),
    "l": Decimal("1000"),
    "tsp": Decimal("4.92892159375"),
    "tbsp": Decimal("14.78676478125"),
    "cup": Decimal("236.5882365"),
    "floz": Decimal("29.5735295625"),
}

#: Units that mean "one of these", where only a stated portion weight helps.
COUNT_UNITS = frozenset({"pinch", "clove", "sprig", "slice", "can", "pkg", ""})

#: A pinch is small enough that precision is pointless and a missing value is
#: more annoying than a rough one. This is the single exception to the
#: no-guessing rule above, and it is capped at a rounding error.
PINCH_GRAMS = Decimal("0.35")


class Unresolved(Exception):
    """Raised when a quantity cannot honestly be expressed in grams."""

    def __init__(self, unit, reason):
        self.unit = unit
        self.reason = reason
        super().__init__(reason)


def kind_of(unit):
    if unit in MASS_TO_GRAMS:
        return "mass"
    if unit in VOLUME_TO_ML:
        return "volume"
    return "count"


def to_decimal(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def to_grams(quantity, unit, *, density_g_per_ml=None, portions=None):
    """Convert ``quantity`` of ``unit`` into grams.

    ``portions`` maps a unit code to grams for one of it, for this food —
    ``{"clove": Decimal("3"), "": Decimal("55")}``. The empty-string key is
    the weight of one unqualified item, so "2 eggs" works.

    Raises ``Unresolved`` rather than guessing.
    """
    quantity = to_decimal(quantity)
    if quantity is None:
        raise Unresolved(unit, "no quantity given")
    unit = unit or ""
    portions = portions or {}

    # A stated portion always wins, including over a mass unit: if someone has
    # recorded that a slice of their bread is 38 g, that beats any general rule.
    if unit in portions and portions[unit]:
        return quantity * to_decimal(portions[unit])

    if unit in MASS_TO_GRAMS:
        return quantity * MASS_TO_GRAMS[unit]

    if unit in VOLUME_TO_ML:
        density = to_decimal(density_g_per_ml)
        if not density:
            raise Unresolved(
                unit,
                f"{unit} is a volume and this food has no density recorded — "
                "add a portion weight or a density to convert it",
            )
        return quantity * VOLUME_TO_ML[unit] * density

    if unit == "pinch":
        return quantity * PINCH_GRAMS

    raise Unresolved(
        unit,
        f"no weight recorded for one {unit or 'item'} of this food — "
        "add a portion weight to convert it",
    )


def scale_per_100g(value_per_100g, grams):
    """The headline arithmetic, in one place so it is only written once."""
    value = to_decimal(value_per_100g)
    grams = to_decimal(grams)
    if value is None or grams is None:
        return None
    return value * grams / Decimal("100")
