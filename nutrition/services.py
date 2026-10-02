"""Working out what is in a food, a meal, or a day.

Everything here returns ``(totals, problems)``. Totals are keyed by nutrient
and held in base units, so a food recorded in micrograms and one recorded in
milligrams add up without either knowing about the other. Problems are the
things that could not be counted — a meal that contains itself, a row with no
source — and they are reported rather than swallowed, because a calorie
figure that quietly omits the olive oil is worse than one that says so.
"""

from collections import defaultdict
from decimal import Decimal

from .models import MAX_MEAL_DEPTH, FoodTracking, Macro, MealFood, Micro


def _key(kind, group, name, unit):
    """What a nutrient is, for totalling purposes.

    Grouped name rather than a bare name: "Fat / Total fat" and
    "Carbohydrate / Total" stay apart, and two foods both listing
    "Saturated fat" under Fat add together as they should.
    """
    return (kind, group, name, unit.dimension)


def _blank():
    return defaultdict(lambda: Decimal("0"))


def blank_totals():
    """An empty totals accumulator, for callers outside this module."""
    return _blank()


def totals_for_food(food, scale=Decimal("1")):
    """Every macro and micro on one food, scaled and in base units."""
    totals = _blank()

    rows = list(
        Macro.objects.filter(food=food).select_related("core_macro", "unit")
    ) + list(Micro.objects.filter(food=food).select_related("category", "unit"))

    for row in rows:
        if isinstance(row, Macro):
            key = _key("macro", row.core_macro.name, row.component_name, row.unit)
        else:
            key = _key("micro", row.category.name, row.micro_name, row.unit)
        totals[key] += row.in_base_units * Decimal(scale)

    return totals, []


def totals_for_meal(meal, scale=Decimal("1"), _seen=None, _depth=0):
    """Everything in a meal, following meals inside it.

    ``_seen`` carries the meals already on this branch. A meal that turns up
    twice on one path is a cycle: the model refuses to create one, but data
    imported or edited around the validation still can, and an infinite loop
    in a page render is a worse answer than a note saying which meal is
    wrong.
    """
    seen = set(_seen or ())
    totals = _blank()
    problems = []

    if meal.pk in seen:
        return totals, [f"{meal.name} contains itself; stopped counting there."]
    if _depth > MAX_MEAL_DEPTH:
        return totals, [f"{meal.name} nests deeper than {MAX_MEAL_DEPTH}; stopped there."]
    seen.add(meal.pk)

    components = MealFood.objects.filter(target_meal=meal).select_related(
        "source_food", "source_meal"
    )

    for component in components:
        part, trouble = totals_for_portion(
            component, scale=Decimal(scale), _seen=seen, _depth=_depth + 1
        )
        for key, value in part.items():
            totals[key] += value
        problems += trouble

    return totals, problems


def totals_for_portion(meal_food, scale=Decimal("1"), _seen=None, _depth=0):
    """One MealFood: its source, times its portion, times any outer scale."""
    factor = Decimal(scale) * meal_food.fraction

    if meal_food.source_is_meal:
        if meal_food.source_meal_id is None:
            return _blank(), ["A meal portion has no meal attached to it."]
        return totals_for_meal(
            meal_food.source_meal, scale=factor, _seen=_seen, _depth=_depth
        )

    if meal_food.source_food_id is None:
        return _blank(), ["A food portion has no food attached to it."]
    return totals_for_food(meal_food.source_food, scale=factor)


def totals_for_tracking(entry):
    """One logged thing: the portion eaten, of the portion recorded."""
    return totals_for_portion(entry.meal_food, scale=Decimal(entry.portion))


def totals_for_day(user, day):
    """Everything one person ate on one date, in their active timezone."""
    from django.utils import timezone

    start = timezone.make_aware(
        timezone.datetime.combine(day, timezone.datetime.min.time()),
        timezone.get_current_timezone(),
    )
    end = start + timezone.timedelta(days=1)

    totals = _blank()
    problems = []

    entries = (
        FoodTracking.objects.filter(user=user, timestamp__gte=start, timestamp__lt=end)
        .select_related("meal_food", "meal_food__source_food", "meal_food__source_meal")
    )

    for entry in entries:
        part, trouble = totals_for_tracking(entry)
        for key, value in part.items():
            totals[key] += value
        problems += trouble

    return totals, problems


def readable(totals):
    """Turn the keyed totals into rows a template can loop over.

    Base units throughout, so the unit is the dimension's own: grams for
    mass, kilocalories for energy. Presenting milligrams is the view's
    business, not the arithmetic's.
    """
    from .models import Dimension

    base = {
        Dimension.MASS: "g",
        Dimension.VOLUME: "ml",
        Dimension.ENERGY: "kcal",
        Dimension.COUNT: "",
    }

    rows = [
        {
            "kind": kind,
            "group": group,
            "name": name,
            "amount": amount,
            "unit": base.get(dimension, ""),
        }
        for (kind, group, name, dimension), amount in totals.items()
    ]
    rows.sort(key=lambda row: (row["kind"] != "macro", row["group"], row["name"]))
    return rows


# --- Turning a recipe line into a portion ------------------------------------
#
# A recipe says "250 g" or "2 tbsp" or "1 clove". A food is a table of figures
# per some reference quantity, usually 100 g. Pairing the two is three steps:
# read the recipe's unit, convert it into the food's, then express the result
# as a multiple of the reference quantity.
#
# Every step can fail for a reason worth naming, and none of them guesses.
# An invented density is worse than a blank, because it looks like an answer.


def unit_by_symbol(symbol):
    """The recipe's unit choices and the nutrition units share their symbols."""
    from .models import Unit

    if not symbol:
        return None
    return Unit.objects.filter(symbol=symbol).first()


#: Units that count things rather than measure them. Each needs a recorded
#: weight for one of whatever it is.
COUNTING = {"ea", "clove", "sprig", "slice", "can", "pkg", "pinch"}


def to_reference_unit(quantity, unit, food):
    """``quantity`` of ``unit``, expressed in the food's reference unit.

    Three hops, each skipped when it is not needed: the amount goes to its
    own dimension's base (gram, millilitre), crosses to the other dimension
    through the food's density if it must, and is then divided into the
    target unit.

    Returns ``(amount, problem)``. Nothing is guessed: a volume with no
    density and a count with no recorded weight both come back as a stated
    gap, because an invented number looks exactly like a measured one.
    """
    if quantity is None:
        return None, "no amount given"
    if unit is None:
        return None, "no unit given"

    target = food.reference_unit
    quantity = Decimal(quantity)

    if unit.dimension == target.dimension:
        return unit.convert(quantity, target), None

    # --- into the base unit of whatever this amount measures ---------------
    if unit.dimension == "count" or unit.symbol in COUNTING:
        portion = food.portions_of.filter(unit=unit).first()
        if portion is None:
            return None, f"no weight recorded for one {unit.symbol}"
        amount, dimension = quantity * Decimal(portion.grams), "mass"
    elif unit.dimension in ("mass", "volume"):
        amount, dimension = unit.to_base_units(quantity), unit.dimension
    else:
        return None, f"{unit.symbol} cannot be converted"

    # --- across, if the food is measured the other way ---------------------
    if dimension != target.dimension:
        if {dimension, target.dimension} != {"mass", "volume"}:
            return None, f"{unit.symbol} and {target.symbol} do not convert"
        if food.density_g_per_ml is None:
            return None, (
                f"no density recorded, so {unit.symbol} cannot become {target.symbol}"
            )
        density = Decimal(food.density_g_per_ml)
        amount = amount * density if dimension == "volume" else amount / density

    # --- and down into the unit actually asked for -------------------------
    return amount / target.to_base, None


def portion_factor(quantity, unit_symbol, food):
    """How many of the food's reference quantities a recipe line calls for.

    250 g of something listed per 100 g is a factor of 2.5 — multiply every
    nutrient by it. Returns ``(factor, problem)``.
    """
    unit = unit_by_symbol(unit_symbol)
    amount, problem = to_reference_unit(quantity, unit, food)
    if problem:
        return None, problem
    if not food.reference_quantity:
        return None, "the food has no reference quantity"
    return amount / Decimal(food.reference_quantity), None


def totals_for_line(quantity, unit_symbol, food):
    """The nutrition a recipe line's worth of a food actually contributes."""
    factor, problem = portion_factor(quantity, unit_symbol, food)
    if problem:
        return _blank(), [problem]
    return totals_for_food(food, scale=factor)
