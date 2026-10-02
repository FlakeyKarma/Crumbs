"""What a recipe adds up to.

Walks the ingredient lines, resolves each to a food, converts the line's
amount into that food's own measure, and sums the lot. Three things decide
whether this is useful or misleading, and all three are visible in the
output rather than smoothed over:

* **A line nobody has matched to a food contributes nothing**, and is named.
  A calorie figure that quietly omits the olive oil is worse than one that
  says it is missing the olive oil.
* **A line whose unit cannot be converted** — two tablespoons of a food with
  no density — is the same kind of gap, and says which.
* **Per serving is the figure people want**, and it does not change when the
  servings dial is turned. The dial rescales what you buy, not what a
  portion contains.
"""

from decimal import Decimal

from .references import library, normalise


def food_for_line(line, user, references=None, bindings=None):
    """The food this ingredient line means, and how that was decided.

    Two routes, in order of how much somebody meant it:

    1. a reference the reader has bound to a food — an explicit choice;
    2. a food whose name is exactly the ingredient's — a lookup, not a
       guess, and labelled as such so an unexpected total can be traced.

    Nothing fuzzier than that. Matching "beef" to "Beef dripping" because
    they share a word would be inventing an answer.
    """
    from nutrition.models import Food

    name = normalise(line.ingredient.name)

    for reference in references if references is not None else library():
        if reference.matches(name):
            chosen = (bindings or {}).get(reference.pk)
            if chosen is not None and chosen.source_food_id:
                return chosen.source_food, "you chose it"

    match = Food.objects.filter(name__iexact=line.ingredient.name).first()
    if match is not None:
        return match, "matched by name"
    return None, None


def recipe_breakdown(recipe, user):
    """Totals for the recipe as written, and for one serving of it.

    Returns groups ready to render, the energy row on its own, and the lines
    that could not be counted.
    """
    from nutrition.services import (
        blank_totals,
        portion_factor,
        readable,
        totals_for_food,
    )

    from .references import bindings_for

    references = library()
    bindings = bindings_for(user, recipe)

    totals = blank_totals()
    gaps = []
    counted = 0

    lines = list(recipe.recipe_ingredients.select_related("ingredient"))
    for line in lines:
        food, how = food_for_line(line, user, references=references, bindings=bindings)
        if food is None:
            gaps.append({"line": line, "reason": "no food chosen for it"})
            continue

        factor, problem = portion_factor(line.quantity, line.unit, food)
        if problem:
            gaps.append({"line": line, "reason": problem})
            continue

        part, _ = totals_for_food(food, scale=factor)
        for key, value in part.items():
            totals[key] += value
        counted += 1

    servings = Decimal(recipe.servings or 1)
    per_serving = {key: value / servings for key, value in totals.items()}

    return {
        "complete": not gaps and bool(lines),
        "counted": counted,
        "lines": len(lines),
        "gaps": gaps,
        "servings": servings,
        # Paired here rather than in the template: looking the whole-recipe
        # figure up per row while rendering is a nested loop over the same
        # data, and the template is the wrong place to notice that.
        "groups": _grouped(readable(totals), readable(per_serving)),
        "energy_whole": _energy(totals),
        "energy_each": _energy(per_serving),
    }


def _energy(totals):
    for (_, group, _, _), value in totals.items():
        if group == "Energy":
            return value
    return None


def _grouped(whole_rows, each_rows):
    """Macronutrients, then each micro category, in the pantry's own order.

    Each row carries both figures, so the template loops once.
    """
    order = ["Macronutrients", "Minerals", "Vitamins"]
    per_serving = {(row["group"], row["name"]): row["amount"] for row in each_rows}
    buckets = {}

    for row in whole_rows:
        if row["group"] == "Energy":
            # Shown on its own above the groups; repeating it inside
            # Macronutrients would read as two measurements.
            continue
        label = "Macronutrients" if row["kind"] == "macro" else row["group"]
        buckets.setdefault(label, []).append(
            {
                "name": row["name"],
                "unit": row["unit"],
                "whole": row["amount"],
                "each": per_serving.get((row["group"], row["name"])),
            }
        )

    seen = [label for label in order if label in buckets]
    seen += [label for label in buckets if label not in order]
    return [{"label": label, "rows": buckets[label]} for label in seen]
