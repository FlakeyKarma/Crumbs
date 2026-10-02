"""Turning logs into a day's picture.

Three jobs: freeze nutrition onto a log entry when it's written, roll a day
up per metric, and say how each figure sits against the target that was in
force *that* day.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Avg, Max, Min, Q, Sum
from django.utils import timezone

from pantry import services as pantry
from pantry.models import MovementKind

from .models import (
    Aggregation,
    Cadence,
    ConsumptionEntry,
    HealthSettings,
    HealthTarget,
    MealSlot,
    Metric,
    Observation,
    TargetMode,
)

ENERGY_SLUG = "energy"


# --- Writing a log entry ----------------------------------------------------


@transaction.atomic
def log_recipe(user, recipe, *, servings=Decimal("1"), slot=MealSlot.DINNER,
               consumed_at=None, deplete_stock=False):
    """Record eating some of a recipe.

    ``deplete_stock`` is off by default because the usual sequence is cook,
    then eat, and cooking already drew the ingredients. Eating a portion of
    something you cooked yesterday should not empty the cupboard twice.
    """
    report = pantry.recipe_nutrition(recipe, user, servings=None)
    servings = Decimal(servings)
    per_batch = report["totals"]
    portion = {
        field: (value * servings / Decimal(recipe.servings or 1))
        for field, value in per_batch.items()
        if value is not None
    }

    entry = ConsumptionEntry.objects.create(
        user=user,
        recipe=recipe,
        servings=servings,
        slot=slot,
        consumed_at=consumed_at or timezone.now(),
        nutrients={field: str(value) for field, value in portion.items()},
        incomplete=not report["complete"],
        label=recipe.title,
    )

    if deplete_stock:
        pantry.cook_from_recipe(recipe, user, servings=servings)

    return entry, report["problems"]


@transaction.atomic
def log_food(user, food, *, grams, slot=MealSlot.SNACK, consumed_at=None, deplete_stock=True):
    """Record eating a food directly — an apple, a yoghurt, a handful of nuts.

    Here ``deplete_stock`` defaults on: eating a thing from the cupboard is
    exactly when the cupboard should lose it.
    """
    grams = Decimal(grams)
    nutrients = {
        field: str(value)
        for field, value in food.nutrients_for(grams).items()
        if value is not None
    }
    entry = ConsumptionEntry.objects.create(
        user=user,
        entry=food,
        grams=grams,
        slot=slot,
        consumed_at=consumed_at or timezone.now(),
        nutrients=nutrients,
        incomplete=not food.has_macros,
        label=str(food),
    )
    if deplete_stock:
        pantry.draw(user, food, grams, kind=MovementKind.EATEN, actor=user)
    return entry


# --- Reading a day back -----------------------------------------------------


def metrics_for(user):
    return Metric.objects.available_to(user).order_by("position", "name")


def target_on(user, metric, day):
    """The target in force for this metric on this day, if any."""
    return (
        HealthTarget.objects.filter(user=user, metric=metric, effective_from__lte=day)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=day))
        .order_by("-effective_from")
        .first()
    )


def _window(day, cadence, week_starts_monday=True):
    if cadence == Cadence.WEEKLY:
        offset = day.weekday() if week_starts_monday else (day.weekday() + 1) % 7
        start = day - timedelta(days=offset)
        return start, start + timedelta(days=6)
    return day, day


def derived_total(user, nutrient_field, start, end):
    """Sum one nutrient across the food log for a date window.

    Summed in Python rather than in SQL because the values are frozen into a
    JSON column, which SQLite cannot aggregate numerically — and freezing was
    the more important property.
    """
    entries = ConsumptionEntry.objects.filter(
        user=user,
        consumed_at__date__gte=start,
        consumed_at__date__lte=end,
    )
    total, seen = Decimal("0"), False
    for entry in entries:
        value = entry.value_of(nutrient_field)
        if value is not None:
            total += value
            seen = True
    return total if seen else None


def observed_value(user, metric, start, end):
    readings = Observation.objects.filter(
        user=user, metric=metric, observed_at__date__gte=start, observed_at__date__lte=end
    )
    if not readings.exists():
        return None
    if metric.aggregation == Aggregation.LAST:
        return readings.order_by("-observed_at").first().value
    key = {
        Aggregation.SUM: Sum("value"),
        Aggregation.MEAN: Avg("value"),
        Aggregation.MAX: Max("value"),
        Aggregation.MIN: Min("value"),
    }[metric.aggregation]
    result = readings.aggregate(value=key)["value"]
    return Decimal(str(result)) if result is not None else None


def evaluate(value, lower, upper, mode):
    """Where a figure sits against its target.

    One vocabulary for all four modes, so the panel can draw one kind of bar:
    ``state`` is under, ok, over or unknown, and ``fraction`` is how far
    along the bar to fill.
    """
    if mode == TargetMode.TRACK or value is None:
        return {"state": "unknown" if value is None else "tracked", "fraction": None}

    if mode == TargetMode.FLOOR:
        if lower is None:
            return {"state": "tracked", "fraction": None}
        return {
            "state": "ok" if value >= lower else "under",
            "fraction": min(float(value / lower), 1.0) if lower else None,
        }

    if mode == TargetMode.CEILING:
        if upper is None:
            return {"state": "tracked", "fraction": None}
        return {
            "state": "ok" if value <= upper else "over",
            "fraction": min(float(value / upper), 1.0) if upper else None,
        }

    # Range.
    if lower is None or upper is None:
        return {"state": "tracked", "fraction": None}
    if value < lower:
        state = "under"
    elif value > upper:
        state = "over"
    else:
        state = "ok"
    return {"state": state, "fraction": min(float(value / upper), 1.0) if upper else None}


#: The order the panel lays groups out in. Nutrition first because that is
#: what logging feeds; the pantry's own grouping is reused so a nutrient sits
#: under the same heading here as it does on a food's page.
GROUP_ORDER = ("Macronutrients", "Minerals", "Vitamins")


def group_of(metric):
    """Which heading a metric belongs under.

    Nutrition metrics borrow the pantry's grouping so the two pages agree;
    anything else falls back to its own kind.
    """
    from pantry.models import FoodEntry

    if metric.nutrient_field:
        for label, fields in FoodEntry.NUTRIENT_GROUPS:
            if metric.nutrient_field in fields:
                return label
    return metric.get_kind_display()


def group_rows(rows):
    """Bucket the panel's rows, keeping targeted measures out in the open.

    Twenty-seven nutrients is more than anyone wants to scroll past. A
    measure you have set a target for is something you are watching, so it
    stays visible; the rest are folded away but still counted, because a
    figure you are not watching is exactly the one you want to be able to
    check.
    """
    seen = []
    for row in rows:
        if row["group"] not in seen:
            seen.append(row["group"])
    ordered = [label for label in GROUP_ORDER if label in seen]
    ordered += [label for label in seen if label not in GROUP_ORDER]

    groups = []
    for label in ordered:
        members = [row for row in rows if row["group"] == label]
        groups.append(
            {
                "label": label,
                "rows": members,
                "tracked": [row for row in members if row["target"]],
                "untracked": [row for row in members if not row["target"]],
                "recorded": [row for row in members if row["value"] is not None],
            }
        )
    return groups


def day_report(user, day=None):
    """Everything the panel draws, for one day."""
    day = day or timezone.localdate()
    prefs = HealthSettings.load(user)
    pinned = set(prefs.show_metrics.values_list("pk", flat=True))

    # The energy target comes first: macros expressed as a share of energy
    # are resolved against it, so it has to be known before the rest.
    energy_metric = metrics_for(user).filter(slug=ENERGY_SLUG).first()
    energy_target = None
    if energy_metric:
        found = target_on(user, energy_metric, day)
        if found:
            energy_target = found.upper or found.lower

    # What the macro plan decides, if it is switched on. It covers energy and
    # the three macros and nothing else; everything beyond that stays with
    # whatever targets were set by hand. One source per measure, so the panel
    # can never show two numbers that disagree.
    planned = plan_targets(user)

    rows = []
    for metric in metrics_for(user):
        target = target_on(user, metric, day)
        cadence = target.cadence if target else Cadence.DAILY
        start, end = _window(day, cadence, prefs.week_starts_monday)

        if metric.is_derived:
            value = derived_total(user, metric.nutrient_field, start, end)
        else:
            value = observed_value(user, metric, start, end)

        lower = upper = None
        mode = target.mode if target else TargetMode.TRACK
        note = ""

        if metric.slug in planned:
            decided = planned[metric.slug]
            lower, upper, mode = decided["lower"], decided["upper"], decided["mode"]
            note = decided["note"]
        elif target:
            lower, upper = target.resolved_bounds(energy_target)

        rows.append(
            {
                "metric": metric,
                "value": value,
                "display": metric.format(value),
                "target": target,
                "lower": lower,
                "upper": upper,
                "cadence": cadence,
                "planned": metric.slug in planned,
                "note": note,
                "pinned": metric.pk in pinned,
                "group": group_of(metric),
                **evaluate(value, lower, upper, mode),
            }
        )

    energy_row = next((row for row in rows if row["metric"].slug == ENERGY_SLUG), None)

    return {
        "day": day,
        "rows": rows,
        # Energy is drawn on its own above everything else: it is the measure
        # the macros are a share of, so burying it among them reads backwards.
        "energy_row": energy_row,
        "groups": group_rows([row for row in rows if row is not energy_row]),
        "pinned": [row for row in rows if row["pinned"]] or rows[:4],
        "entries": ConsumptionEntry.objects.filter(
            user=user, consumed_at__date=day
        ).order_by("consumed_at"),
        "incomplete": ConsumptionEntry.objects.filter(
            user=user, consumed_at__date=day, incomplete=True
        ).exists(),
        "settings": prefs,
    }


def weight_trend(user, *, days=None):
    """A rolling mean of body weight.

    Daily weight is mostly water, and showing the raw line makes people
    despair on a Tuesday. The mean is what actually moves.
    """
    prefs = HealthSettings.load(user)
    window = days or prefs.weight_smoothing_days
    metric = metrics_for(user).filter(slug="weight").first()
    if metric is None:
        return []

    today = timezone.localdate()
    readings = Observation.objects.filter(
        user=user, metric=metric, observed_at__date__gte=today - timedelta(days=window * 4)
    ).order_by("observed_at")

    points = []
    values = []
    for reading in readings:
        values.append(reading.value)
        values = values[-window:]
        points.append(
            {
                "day": timezone.localtime(reading.observed_at).date(),
                "value": reading.value,
                "mean": sum(values) / len(values),
            }
        )
    return points


# --- Calories and macros, kept in step ---------------------------------------
#
# Grams times four, four and nine. Two views of one thing, so setting both by
# hand means maintaining two numbers that can disagree; MacroPlan picks which
# one you decided and derives the other.

MACROS = ("protein", "carbohydrate", "fat")


def _energy_per_gram(metrics):
    """kcal per gram for each macro, from the metric rows themselves.

    Read rather than hard-coded, because the figures are already on the
    metrics and a second copy here is a second thing to keep right.
    """
    return {
        slug: Decimal(metrics[slug].energy_per_gram)
        for slug in MACROS
        if slug in metrics and metrics[slug].energy_per_gram
    }


def plan_targets(user, metrics=None):
    """Targets the plan decides, keyed by metric slug.

    Returns ``{slug: {"lower": …, "upper": …, "mode": …, "note": …}}`` for
    the four it covers, or an empty dict when the plan is off. Everything
    else stays with whatever HealthTarget rows say.
    """
    from .models import MacroPlan, Metric, PlanMode, TargetMode

    plan = MacroPlan.for_user(user)
    if plan is None or plan.mode == PlanMode.OFF:
        return {}

    if metrics is None:
        metrics = {
            metric.slug: metric
            for metric in Metric.objects.filter(slug__in=("energy",) + MACROS)
        }
    per_gram = _energy_per_gram(metrics)
    if len(per_gram) < len(MACROS):
        return {}

    if plan.mode == PlanMode.FROM_CALORIES:
        return _macros_from_calories(plan, per_gram)
    return _calories_from_macros(user, plan, per_gram, metrics)


def _macros_from_calories(plan, per_gram):
    """Split a calorie range by the percentages, into grams.

    The widest honest band: the fewest grams is the smallest share of the
    fewest calories, and the most is the largest share of the most. Pairing
    them any other way would claim a precision the ranges do not have.
    """
    from .models import TargetMode

    lower_kcal = plan.energy_lower
    upper_kcal = plan.energy_upper
    shares = plan.percentages

    targets = {
        "energy": {
            "lower": lower_kcal,
            "upper": upper_kcal,
            "mode": _mode_for(lower_kcal, upper_kcal),
            "note": "set by you",
        }
    }

    for slug in MACROS:
        low_pct, high_pct = shares[slug]
        grams_low = (
            (Decimal(lower_kcal) * low_pct / Decimal(100) / per_gram[slug])
            if lower_kcal is not None
            else None
        )
        grams_high = (
            (Decimal(upper_kcal) * high_pct / Decimal(100) / per_gram[slug])
            if upper_kcal is not None
            else None
        )
        targets[slug] = {
            "lower": _round(grams_low),
            "upper": _round(grams_high),
            "mode": _mode_for(grams_low, grams_high),
            "note": f"{low_pct:g}–{high_pct:g}% of calories",
        }
    return targets


def _calories_from_macros(user, plan, per_gram, metrics):
    """Add the macro targets up into a calorie range.

    A macro with no target of its own contributes nothing, and the result
    says so — a calorie figure that quietly omits the fat is worse than one
    that admits it is partial.
    """
    lower = Decimal("0")
    upper = Decimal("0")
    missing = []

    for slug in MACROS:
        metric = metrics.get(slug)
        target = target_on(user, metric) if metric else None
        if target is None or (target.lower is None and target.upper is None):
            missing.append(slug)
            continue
        if target.lower is not None:
            lower += Decimal(target.lower) * per_gram[slug]
        if target.upper is not None:
            upper += Decimal(target.upper) * per_gram[slug]

    if len(missing) == len(MACROS):
        return {}

    note = "added up from your macros"
    if missing:
        note += f" — no target set for {', '.join(missing)}"

    return {
        "energy": {
            "lower": _round(lower) or None,
            "upper": _round(upper) or None,
            "mode": _mode_for(lower or None, upper or None),
            "note": note,
        }
    }


def _round(value):
    if value is None:
        return None
    return Decimal(value).quantize(Decimal("0.1"))


def _mode_for(lower, upper):
    from .models import TargetMode

    if lower is not None and upper is not None:
        return TargetMode.RANGE
    if lower is not None:
        return TargetMode.FLOOR
    if upper is not None:
        return TargetMode.CEILING
    return TargetMode.TRACK
