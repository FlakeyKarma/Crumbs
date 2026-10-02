"""What the Pantry does, separated from how it is asked.

Views here are thin on purpose — every one of these functions is also reached
from the JSON API the phone client uses, and from the health app when a meal
is logged, so none of it can live in a view.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from .models import (
    FoodEntry,
    FoodSource,
    IngredientDefault,
    MovementKind,
    PantryItem,
    StockMovement,
    UserAPICredential,
)
from .modules import SourceError, all_modules, get_module
from .modules.units import Unresolved, to_grams

# --- Credentials ------------------------------------------------------------


def api_key_for(user, api):
    if not (user and user.is_authenticated):
        return None
    credential = UserAPICredential.objects.filter(user=user, api=api).first()
    if credential is None:
        return None
    UserAPICredential.objects.filter(pk=credential.pk).update(last_used_at=timezone.now())
    return credential.key


def source_status(user):
    """Which sources this user can actually reach, for the picker to say so."""
    return [
        {
            "api": module.api_code,
            "label": module.label,
            "requires_key": module.requires_key,
            "usable": not module.requires_key or bool(api_key_for(user, module.api_code)),
            "supports_barcode": module.supports_barcode,
        }
        for module in all_modules()
    ]


# --- Looking food up --------------------------------------------------------


def search_local(*, barcode=None, term=None, company=None, limit=25):
    """The catalogue first, always.

    Local before external, not both at once: most lookups are for food
    already imported, and firing an API call for every keystroke is both slow
    and rude to a free service.

    ``company`` narrows to one maker and combines with ``term``, so "oats"
    from "Flahavan" is one query rather than a search followed by squinting.
    """
    queryset = FoodEntry.objects.all()
    if barcode:
        queryset = queryset.filter(barcode=str(barcode).strip())
        return list(queryset[:limit])

    if not term and not company:
        return []
    if term:
        queryset = queryset.filter(Q(name__icontains=term) | Q(brand__icontains=term))
    if company:
        queryset = queryset.filter(brand__icontains=str(company).strip())
    return list(queryset[:limit])


def search_companies(term, *, limit=25):
    """Companies whose name contains ``term``, case-insensitively.

    Brands are free text off two APIs, so the same company arrives as
    "Tesco", "TESCO" and "tesco". Grouping in the database would treat those
    as three; folding on the lowercased name and keeping the most common
    spelling as the label gives one row that says what people actually wrote.
    """
    term = " ".join(str(term or "").split())
    if not term:
        return []

    rows = (
        FoodEntry.objects.filter(brand__icontains=term)
        .exclude(brand="")
        .values_list("brand", flat=True)
    )

    folded = {}
    for brand in rows:
        key = brand.casefold()
        bucket = folded.setdefault(key, {"spellings": {}, "count": 0})
        bucket["count"] += 1
        bucket["spellings"][brand] = bucket["spellings"].get(brand, 0) + 1

    companies = [
        {
            "name": max(bucket["spellings"].items(), key=lambda pair: pair[1])[0],
            "count": bucket["count"],
        }
        for bucket in folded.values()
    ]
    companies.sort(key=lambda row: (-row["count"], row["name"].casefold()))
    return companies[:limit]


def search_external(user, *, barcode=None, term=None, limit=25):
    """Ask every source that can answer. Returns ``(results, errors)``.

    A source failing is not the lookup failing. No FDC key is the common
    case, and it must not stop Open Food Facts from answering — so errors
    come back alongside results rather than instead of them.
    """
    results, errors = [], []
    for module in all_modules():
        if barcode and not module.supports_barcode:
            continue
        key = api_key_for(user, module.api_code) if module.requires_key else None
        if module.requires_key and not key:
            errors.append({"api": module.api_code, "error": "missing_key"})
            continue
        try:
            if barcode:
                found = module.search_by_barcode(barcode, api_key=key)
            else:
                found = module.search_by_name(term, api_key=key, page_size=limit)
        except SourceError as exc:
            errors.append({"api": module.api_code, "error": str(exc)})
            continue
        results.extend(found)
    return results, errors


def cluster(results):
    """Group near-identical results so the picker offers one choice, not five.

    The same product from both sources is two honest rows with two sets of
    numbers — we keep both, but they belong side by side under one heading,
    not as separate options someone has to compare by eye.
    """
    clusters = {}
    for result in results:
        clusters.setdefault(result.fingerprint, []).append(result)
    return [
        {"members": members, "duplicate": len(members) > 1, "lead": members[0]}
        for members in clusters.values()
    ]


@transaction.atomic
def import_food(user, api, external_id):
    """Refetch a food by id and store it.

    Deliberately refetches rather than trusting a payload posted by the
    client: the browser is not a source of nutrition data. Re-importing the
    same food updates the one entry row and appends another provenance row.
    """
    module = get_module(api)
    if module is None:
        raise SourceError(api, "unknown source")

    key = api_key_for(user, api) if module.requires_key else None
    if module.requires_key and not key:
        raise SourceError(api, "missing_key")

    food = module.fetch_one(external_id, api_key=key)
    if food is None:
        raise SourceError(api, "that food is no longer available from this source")

    entry, _ = FoodEntry.all_objects.update_or_create(
        source_api=food.source_api,
        external_id=food.external_id,
        defaults={
            "name": food.name,
            "brand": food.brand,
            "barcode": food.barcode,
            "serving_size": food.serving_size,
            "serving_unit": food.serving_unit,
            "is_active": True,
            **{field: getattr(food, field) for field in FoodEntry.NUTRIENTS},
        },
    )
    FoodSource.objects.create(
        entry=entry,
        api=food.source_api,
        external_id=food.external_id,
        fetched_by=user if getattr(user, "is_authenticated", False) else None,
        raw_payload=food.raw,
    )
    return entry


# --- Resolving a recipe line -----------------------------------------------


def entry_for(user, ingredient):
    """Which food this user means by this recipe ingredient, if they've said."""
    default = (
        IngredientDefault.objects.select_related("entry")
        .filter(user=user, ingredient=ingredient)
        .first()
    )
    return default.entry if default else None


def line_grams(recipe_ingredient, entry):
    """Grams for one recipe line, or ``None`` with the reason.

    Returns ``(grams, problem)``. A line we cannot convert is not an error —
    it is a gap the page should show, because the cure is a portion weight,
    not a fallback guess.
    """
    if entry is None:
        return None, "no food chosen for this ingredient"
    try:
        return (
            to_grams(
                recipe_ingredient.quantity,
                recipe_ingredient.unit,
                density_g_per_ml=entry.density_g_per_ml,
                portions=entry.portion_map(),
            ),
            None,
        )
    except Unresolved as exc:
        return None, exc.reason


def recipe_nutrition(recipe, user, *, servings=None):
    """Totals for a whole recipe, plus every line that could not be resolved.

    Reporting the gaps alongside the total is the point. A calorie figure
    that quietly omits the olive oil is worse than one that says so.
    """
    totals = {field: Decimal("0") for field in FoodEntry.NUTRIENTS}
    known = {field: False for field in FoodEntry.NUTRIENTS}
    problems = []

    for line in recipe.recipe_ingredients.select_related("ingredient"):
        entry = entry_for(user, line.ingredient)
        grams, problem = line_grams(line, entry)
        if grams is None:
            problems.append({"ingredient": line.ingredient.name, "reason": problem})
            continue
        for field, value in entry.nutrients_for(grams).items():
            if value is not None:
                totals[field] += value
                known[field] = True

    scale = Decimal("1")
    if servings and recipe.servings:
        scale = Decimal(servings) / Decimal(recipe.servings)

    per_portion = {
        field: (totals[field] * scale if known[field] else None) for field in totals
    }
    return {"totals": per_portion, "problems": problems, "complete": not problems}


# --- Stock ------------------------------------------------------------------


@transaction.atomic
def record(item, kind, grams, *, actor=None, recipe=None, note=""):
    """Move stock and write the ledger row, together or not at all."""
    grams = Decimal(grams)
    movement = StockMovement.objects.create(
        item=item, kind=kind, grams=grams, actor=actor, recipe=recipe, note=note
    )
    item.quantity_g = (item.quantity_g or Decimal("0")) + grams
    item.save(update_fields=["quantity_g"])
    return movement


def stock_of(user, entry):
    """How much of one food this user has, across every lot."""
    total = PantryItem.objects.filter(owner=user, entry=entry).aggregate(
        total=Sum("quantity_g")
    )["total"]
    return total or Decimal("0")


def lots_for(user, entry):
    """This food's lots, soonest to expire first — what to open next.

    Lots with no expiry sort last: an undated bag of rice should not be
    opened ahead of yoghurt that goes off on Thursday.
    """
    return PantryItem.objects.filter(owner=user, entry=entry, quantity_g__gt=0).order_by(
        F("expires_on").asc(nulls_last=True), "acquired_on"
    )


@transaction.atomic
def draw(user, entry, grams, *, kind=MovementKind.CONSUME, actor=None, recipe=None):
    """Take grams of a food from stock, oldest-expiring lot first.

    Returns ``(drawn, short)``. Running out is not an exception: you can cook
    something you are short of, and the pantry's job is to record what
    actually left, not to refuse.
    """
    wanted = Decimal(grams)
    drawn = Decimal("0")
    for lot in lots_for(user, entry).select_for_update():
        if wanted <= 0:
            break
        take = min(lot.quantity_g, wanted)
        record(lot, kind, -take, actor=actor or user, recipe=recipe)
        drawn += take
        wanted -= take
    return drawn, wanted


@transaction.atomic
def cook_from_recipe(recipe, user, *, servings=None):
    """Draw every resolvable ingredient for a batch. Non-fatal throughout.

    Cooking with something you forgot to log is normal, so a line that cannot
    be resolved or is out of stock is reported, not raised.
    """
    drawn, shortfalls = [], []
    scale = Decimal("1")
    if servings and recipe.servings:
        scale = Decimal(servings) / Decimal(recipe.servings)

    for line in recipe.recipe_ingredients.select_related("ingredient"):
        entry = entry_for(user, line.ingredient)
        grams, problem = line_grams(line, entry)
        if grams is None:
            shortfalls.append({"ingredient": line.ingredient.name, "reason": problem})
            continue
        needed = grams * scale
        took, short = draw(user, entry, needed, recipe=recipe, actor=user)
        drawn.append({"ingredient": line.ingredient.name, "grams": took})
        if short > 0:
            shortfalls.append(
                {
                    "ingredient": line.ingredient.name,
                    "reason": f"{short:.0f} g short",
                    "short_g": short,
                }
            )
    return {"drawn": drawn, "shortfalls": shortfalls}


# --- What can I cook --------------------------------------------------------


def availability(recipe, user, *, servings=None):
    """How much of this recipe the user's stock covers.

    ``ratio`` is the share of resolvable lines fully in stock. Unresolvable
    lines are counted separately rather than folded in, because "I can't tell"
    and "you haven't got it" are different answers.
    """
    scale = Decimal("1")
    if servings and recipe.servings:
        scale = Decimal(servings) / Decimal(recipe.servings)

    have, missing, unknown = [], [], []
    for line in recipe.recipe_ingredients.select_related("ingredient"):
        entry = entry_for(user, line.ingredient)
        grams, problem = line_grams(line, entry)
        if grams is None:
            unknown.append({"ingredient": line.ingredient.name, "reason": problem})
            continue
        needed = grams * scale
        held = stock_of(user, entry)
        row = {"ingredient": line.ingredient.name, "needed_g": needed, "held_g": held}
        (have if held >= needed else missing).append(row)

    resolvable = len(have) + len(missing)
    ratio = (len(have) / resolvable) if resolvable else 0.0
    return {
        "have": have,
        "missing": missing,
        "unknown": unknown,
        "ratio": ratio,
        "cookable": not missing and not unknown,
    }


def cookable_recipes(user, recipes):
    """Rank recipes by how much of each the user already has in.

    The point of tracking stock: "what can I make tonight" answered against
    the cupboard rather than against a wish list.
    """
    scored = []
    for recipe in recipes:
        report = availability(recipe, user)
        scored.append({"recipe": recipe, **report})
    scored.sort(key=lambda row: (-row["ratio"], len(row["unknown"]), row["recipe"].title))
    return scored


def expiring_soon(user, *, within_days=7):
    cutoff = timezone.localdate() + timedelta(days=within_days)
    return (
        PantryItem.objects.filter(owner=user, quantity_g__gt=0, expires_on__lte=cutoff)
        .select_related("entry")
        .order_by("expires_on")
    )
