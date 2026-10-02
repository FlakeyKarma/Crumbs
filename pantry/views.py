"""Pantry views.

Plain Django views and ``JsonResponse`` — Crumbs has no DRF and does not need
it for eight endpoints. The JSON half exists because barcode scanning happens
on a phone: the client sends a barcode, the server does the lookups, and no
API key ever leaves the server.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_GET, require_POST

from recipes.models import Ingredient, Recipe

from . import services
from .forms import (
    ReferenceForm,
    ReferencePatternForm,
    CredentialForm,
    DefaultForm,
    FoodEntryForm,
    FoodPortionForm,
    PantryItemForm,
)
from .models import (
    FoodEntry,
    IngredientDefault,
    MovementKind,
    PantryItem,
    SourceAPI,
    UserAPICredential,
)
from .modules import SourceError


def _decimal(raw, default=None):
    try:
        return Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return default


# --- Pages ------------------------------------------------------------------


@login_required
def pantry_home(request):
    lots = (
        PantryItem.objects.filter(owner=request.user, quantity_g__gt=0)
        .select_related("entry")
        .order_by("expires_on", "entry__name")
    )
    totals = (
        PantryItem.objects.filter(owner=request.user, quantity_g__gt=0)
        .values("entry__id", "entry__name", "entry__brand")
        .annotate(grams=Sum("quantity_g"))
        .order_by("entry__name")
    )
    return render(
        request,
        "pantry/home.html",
        {
            "lots": lots,
            "totals": totals,
            "expiring": services.expiring_soon(request.user),
            "page_title": "The Pantry",
        },
    )


@login_required
def what_can_i_cook(request):
    """Recipes ranked by how much of each is already in the cupboard."""
    recipes = (
        Recipe.objects.visible_to(request.user)
        .prefetch_related("recipe_ingredients__ingredient")[:60]
    )
    scored = services.cookable_recipes(request.user, recipes)
    return render(
        request,
        "pantry/cook.html",
        {"scored": scored, "page_title": "What can I cook?"},
    )


@login_required
def food_search(request):
    term = request.GET.get("q", "").strip()
    barcode = request.GET.get("barcode", "").strip()
    company = request.GET.get("company", "").strip()
    external = request.GET.get("external") == "1"

    per_page = _page_size(request)

    local = services.search_local(
        barcode=barcode or None,
        term=term or None,
        company=company or None,
        limit=MAX_RESULTS,
    )
    # Which companies match what was typed, whichever box it was typed in.
    companies = services.search_companies(company or term)
    clusters, errors = [], []
    if external and (term or barcode):
        found, errors = services.search_external(
            request.user, barcode=barcode or None, term=term or None
        )
        clusters = services.cluster(found)

    # Two lists on one page, so two page numbers. Turning the page on the
    # open-source results should not send the catalogue back to page one.
    local_page = _paginate(local, per_page, request.GET.get("page", 1))
    cluster_page = _paginate(clusters, per_page, request.GET.get("xpage", 1))

    return render(
        request,
        "pantry/search.html",
        {
            "term": term,
            "barcode": barcode,
            "company": company,
            "companies": companies,
            "local": local_page,
            "local_page": local_page,
            "clusters": cluster_page,
            "cluster_page": cluster_page,
            "per_page": per_page,
            "page_sizes": PAGE_SIZES,
            "import_cap": MAX_IMPORTS_PER_SUBMIT,
            "errors": errors,
            "searched_externally": external,
            "sources": services.source_status(request.user),
            "page_title": "Find a food",
        },
    )


#: Each import is a fresh fetch from a free API, so a submission is capped.
#: Politeness to the source, and a guard against a "select all" on a page of
#: fifty turning into fifty sequential HTTP calls while the browser waits.
MAX_IMPORTS_PER_SUBMIT = 20

#: How many results a page may show. The largest is above the import cap on
#: purpose — browsing a long list and importing from it are different jobs,
#: and the page says so rather than shrinking to fit the smaller one.
PAGE_SIZES = (10, 25, 50, 100)
DEFAULT_PAGE_SIZE = 25

#: Fetched before paging. A ceiling on the query, not on what you can see.
MAX_RESULTS = 200


def _page_size(request):
    try:
        wanted = int(request.GET.get("per_page", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return wanted if wanted in PAGE_SIZES else DEFAULT_PAGE_SIZE


def _paginate(items, per_page, number):
    """One page of a list, never raising on a silly page number.

    A page out of range is a stale link or a hand-edited URL, not an error
    worth a 404 — the last page is what the reader wanted.
    """
    paginator = Paginator(items, per_page)
    try:
        return paginator.page(int(number))
    except (PageNotAnInteger, TypeError, ValueError):
        return paginator.page(1)
    except EmptyPage:
        return paginator.page(paginator.num_pages)


def _parse_selection(token):
    """'OFF:301762' -> ('OFF', '301762'), or None if it isn't one of ours."""
    api, _, external_id = str(token).partition(":")
    if api in SourceAPI.values and external_id:
        return api, external_id
    return None


@login_required
@require_POST
def import_foods(request):
    """Import one food or a batch of them, then go back to the search.

    A form post, not the JSON endpoint: `api_import` answers the phone client
    and returns JSON, which a browser form would render as a page of braces.

    `only` is a single row's button; `selected` is the checkboxes. The button
    wins, so ticking three boxes and then pressing one row's Import does what
    it looks like rather than importing all four.
    """
    single = request.POST.get("only")
    tokens = [single] if single else request.POST.getlist("selected")

    parsed, unusable = [], 0
    for token in tokens:
        pair = _parse_selection(token)
        if pair:
            parsed.append(pair)
        else:
            unusable += 1

    if not parsed:
        messages.error(request, "Nothing was selected to import.")
        return _back_to_search(request)

    capped = len(parsed) > MAX_IMPORTS_PER_SUBMIT
    parsed = parsed[:MAX_IMPORTS_PER_SUBMIT]

    imported, failures = [], []
    for api, external_id in parsed:
        try:
            entry = services.import_food(request.user, api, external_id)
        except SourceError as exc:
            # One source failing is not the batch failing — the rest still
            # import, and the page says which did not.
            failures.append(f"{api} {external_id}: {exc}")
        else:
            imported.append(entry)

    if imported:
        if len(imported) == 1:
            messages.success(request, f"Imported {imported[0]}.")
        else:
            messages.success(request, f"Imported {len(imported)} foods.")
    for problem in failures:
        messages.error(request, problem)
    if unusable:
        messages.error(request, f"{unusable} selection(s) were not recognised.")
    if capped:
        messages.warning(
            request,
            f"Only the first {MAX_IMPORTS_PER_SUBMIT} were imported — "
            "select fewer and go again.",
        )

    return _back_to_search(request)


def _back_to_search(request):
    """Return to the search that produced these results, filters intact."""
    kept = {
        field: request.POST.get(field, "")
        for field in ("q", "company", "barcode", "external")
        if request.POST.get(field)
    }
    target = reverse("pantry:search")
    return redirect(f"{target}?{urlencode(kept)}" if kept else target)


@login_required
def food_detail(request, pk):
    entry = get_object_or_404(FoodEntry.all_objects, pk=pk)
    return render(
        request,
        "pantry/entry.html",
        {
            "entry": entry,
            "lots": PantryItem.objects.filter(owner=request.user, entry=entry, quantity_g__gt=0),
            "held_g": services.stock_of(request.user, entry),
            "stock_form": PantryItemForm(),
            "portion_form": FoodPortionForm(),
            "history": entry.sources.select_related("fetched_by")[:5],
            "page_title": str(entry),
        },
    )


@login_required
def food_create(request):
    if request.method == "POST":
        form = FoodEntryForm(request.POST, request.FILES)
        if form.is_valid():
            entry = form.save()
            messages.success(request, f"Added {entry}.")
            return redirect(entry)
        messages.error(request, "Something in the food needs fixing.")
    else:
        form = FoodEntryForm()
    return render(
        request, "pantry/entry_form.html", {"form": form, "page_title": "Add a food by hand"}
    )


@login_required
@require_POST
def stock_add(request, pk):
    entry = get_object_or_404(FoodEntry, pk=pk)
    form = PantryItemForm(request.POST)
    if form.is_valid():
        lot = form.save(commit=False)
        lot.owner = request.user
        lot.entry = entry
        opening = lot.quantity_g
        lot.quantity_g = Decimal("0")
        lot.save()
        # The lot starts empty and is filled by a ledger row, so the opening
        # balance has the same provenance as everything after it.
        services.record(lot, MovementKind.ADD, opening, actor=request.user, note="opening")
        messages.success(request, f"Put {opening:.0f} g of {entry} away.")
    else:
        messages.error(request, "That didn't look right — check the amount and dates.")
    return redirect(entry)


@login_required
@require_POST
def portion_add(request, pk):
    entry = get_object_or_404(FoodEntry, pk=pk)
    form = FoodPortionForm(request.POST)
    if form.is_valid():
        portion = form.save(commit=False)
        portion.entry = entry
        portion.save()
        messages.success(request, f"Noted: {portion}.")
    else:
        messages.error(request, "A portion needs a unit and a weight.")
    return redirect(entry)


@login_required
def food_edit(request, pk):
    """Change a food's numbers after the fact.

    Worth having precisely because the entry form now has twenty-seven
    nutrients: nobody types all of them off a packet in one sitting, and
    filling the vitamins in next week should not mean re-entering the food.

    Edits reach everyone, since the catalogue is shared — but already-logged
    meals keep the numbers they were logged with, because health freezes
    nutrition onto each entry at the time.
    """
    entry = get_object_or_404(FoodEntry.all_objects, pk=pk)

    if request.method == "POST":
        form = FoodEntryForm(request.POST, request.FILES, instance=entry)
        if form.is_valid():
            form.save()
            messages.success(request, f"Updated {entry}.")
            return redirect(entry)
        messages.error(request, "Something in the food needs fixing.")
    else:
        form = FoodEntryForm(instance=entry)

    return render(
        request,
        "pantry/entry_form.html",
        {"form": form, "entry": entry, "page_title": f"Edit {entry}"},
    )


@login_required
@require_POST
def stock_move(request, pk):
    """Use, eat, bin or correct a lot."""
    lot = get_object_or_404(PantryItem, pk=pk, owner=request.user)
    kind = request.POST.get("kind")
    if kind not in MovementKind.values:
        raise Http404("No such movement.")
    grams = _decimal(request.POST.get("grams"))
    if grams is None or grams <= 0:
        messages.error(request, "How many grams?")
        return redirect(lot.entry)

    signed = grams if kind == MovementKind.ADD else -min(grams, lot.quantity_g)
    services.record(lot, kind, signed, actor=request.user)
    messages.success(request, f"{lot.entry}: {MovementKind(kind).label.lower()}.")
    return redirect(lot.entry)


@login_required
def choose_default(request, ingredient_id):
    """Pin a recipe ingredient to the food it should mean for this user."""
    ingredient = get_object_or_404(Ingredient, pk=ingredient_id)
    entries = FoodEntry.objects.filter(name__icontains=ingredient.name)[:50]

    if request.method == "POST":
        form = DefaultForm(request.POST, entries=FoodEntry.objects.all())
        if form.is_valid():
            IngredientDefault.objects.update_or_create(
                user=request.user,
                ingredient=ingredient,
                defaults={"entry": form.cleaned_data["entry"]},
            )
            messages.success(request, f"“{ingredient.name}” now means {form.cleaned_data['entry']}.")
            return redirect(request.POST.get("next") or "pantry:home")
    else:
        form = DefaultForm(entries=entries)

    return render(
        request,
        "pantry/choose_default.html",
        {
            "ingredient": ingredient,
            "form": form,
            "suggestions": entries,
            "current": services.entry_for(request.user, ingredient),
            "page_title": f"What is “{ingredient.name}”?",
        },
    )


@login_required
def credentials(request):
    existing = {c.api: c for c in UserAPICredential.objects.filter(user=request.user)}

    if request.method == "POST":
        if "clear" in request.POST:
            UserAPICredential.objects.filter(user=request.user, api=request.POST["clear"]).delete()
            messages.success(request, "Key removed.")
            return redirect("pantry:credentials")
        form = CredentialForm(request.POST)
        if form.is_valid():
            UserAPICredential.objects.update_or_create(
                user=request.user, api=SourceAPI.FDC, defaults={"key": form.cleaned_data["key"]}
            )
            messages.success(request, "Key saved, encrypted.")
            return redirect("pantry:credentials")
    else:
        form = CredentialForm()

    return render(
        request,
        "pantry/credentials.html",
        {
            "form": form,
            "existing": existing,
            "sources": services.source_status(request.user),
            "page_title": "Pantry sources",
        },
    )


# --- JSON -------------------------------------------------------------------


def _payload(request):
    if request.content_type == "application/json" and request.body:
        try:
            return json.loads(request.body)
        except ValueError:
            return {}
    return request.POST.dict()


@login_required
@require_GET
def api_lookup(request):
    """``?barcode=`` or ``?term=``, plus ``external=1`` to reach out.

    Local first and external only on request: most scans are for food already
    imported, and a free service should not be hit on every keystroke.
    """
    barcode = request.GET.get("barcode", "").strip()
    term = request.GET.get("term", "").strip()
    if not (barcode or term):
        return JsonResponse({"error": "give a barcode or a term"}, status=400)

    local = services.search_local(barcode=barcode or None, term=term or None)
    body = {
        "local": [
            {
                "id": entry.pk,
                "name": entry.name,
                "brand": entry.brand,
                "barcode": entry.barcode,
                "source": entry.source_api,
                "energy_kcal": str(entry.energy_kcal) if entry.energy_kcal is not None else None,
                "has_macros": entry.has_macros,
            }
            for entry in local
        ],
        "external": [],
        "errors": [],
    }

    if request.GET.get("external") == "1":
        found, errors = services.search_external(
            request.user, barcode=barcode or None, term=term or None
        )
        body["errors"] = errors
        body["external"] = [
            {
                "duplicate": group["duplicate"],
                "options": [
                    {**food.as_dict(), "has_macros": food.has_macros}
                    for food in group["members"]
                ],
            }
            for group in services.cluster(found)
        ]
    return JsonResponse(body)


@login_required
@require_POST
def api_import(request):
    """``{"source_api": "OFF", "external_id": "..."}`` — the server refetches."""
    data = _payload(request)
    api, external_id = data.get("source_api"), data.get("external_id")
    if not (api and external_id):
        return JsonResponse({"error": "source_api and external_id are required"}, status=400)
    try:
        entry = services.import_food(request.user, api, str(external_id))
    except SourceError as exc:
        status = 422 if str(exc) == "missing_key" else 502
        return JsonResponse({"error": str(exc), "api": exc.api_code}, status=status)
    return JsonResponse({"id": entry.pk, "name": entry.name, "brand": entry.brand}, status=201)


@login_required
@require_POST
def api_stock(request):
    """``{"entry": 12, "grams": 500}`` to add, or negative grams to draw down."""
    data = _payload(request)
    entry = get_object_or_404(FoodEntry, pk=data.get("entry"))
    grams = _decimal(data.get("grams"))
    if grams is None or grams == 0:
        return JsonResponse({"error": "grams is required and cannot be zero"}, status=400)

    if grams > 0:
        lot = PantryItem.objects.create(owner=request.user, entry=entry, quantity_g=Decimal("0"))
        services.record(lot, MovementKind.ADD, grams, actor=request.user)
        drawn, short = grams, Decimal("0")
    else:
        kind = data.get("kind", MovementKind.EATEN)
        if kind not in MovementKind.values:
            kind = MovementKind.EATEN
        drawn, short = services.draw(request.user, entry, -grams, kind=kind, actor=request.user)

    return JsonResponse(
        {
            "entry": entry.pk,
            "moved_g": str(drawn),
            "short_g": str(short),
            "held_g": str(services.stock_of(request.user, entry)),
        }
    )


# --- References -------------------------------------------------------------
#
# The pattern library lives here rather than in the recipes app because it is
# vocabulary, like the food catalogue: shared across the installation, edited
# in one place, and nothing to do with any one recipe.


@login_required
def references(request):
    """The reference library: terms, their phrasings, and their portions."""
    from django.db.models import Q

    from nutrition.models import MealFood
    from recipes.models import Reference
    from recipes.references import collisions

    term = " ".join(request.GET.get("q", "").split())

    library = Reference.objects.prefetch_related(
        "patterns",
        "food_references__meal_food__source_food",
        "food_references__meal_food__source_meal",
    )
    if term:
        # The term or any of its phrasings: looking up "mince" should find
        # the reference called "Lean beef" that lists it.
        library = library.filter(
            Q(label__icontains=term) | Q(patterns__pattern__icontains=term)
        ).distinct()
    library = list(library)

    if request.method == "POST":
        form = ReferenceForm(request.POST)
        if form.is_valid():
            reference = form.save()
            messages.success(request, f"Added {reference}.")
            return redirect("pantry:references")
        messages.error(request, "That reference needs fixing.")
    else:
        form = ReferenceForm(initial={"label": request.GET.get("phrase", "")})

    return render(
        request,
        "pantry/references.html",
        {
            "library": library,
            "form": form,
            "pattern_form": ReferencePatternForm(),
            "search_term": term,
            "total": Reference.objects.count(),
            # First match wins, so two references claiming one phrase is
            # worth surfacing: nothing else would tell you which won.
            "collisions": collisions() if not term else [],
            "meal_foods": MealFood.objects.select_related(
                "source_food", "source_meal"
            )[:200],
            "page_title": "References",
        },
    )


@login_required
@require_POST
def reference_pattern_add(request, pk):
    """Add another way of writing a reference's term."""
    from recipes.models import Reference
    from recipes.references import forget

    reference = get_object_or_404(Reference, pk=pk)
    form = ReferencePatternForm(request.POST)
    if form.is_valid():
        row = form.save(commit=False)
        row.reference = reference
        row.position = reference.patterns.count()
        row.save()
        forget()
        messages.success(request, f"“{row.pattern}” now matches {reference}.")
    else:
        for error in form.errors.get("pattern", ["That phrasing needs fixing."]):
            messages.error(request, error)
    return redirect(f"{reverse('pantry:references')}#reference-{reference.pk}")


@login_required
@require_POST
def reference_pattern_edit(request, pk):
    """Change a phrasing's text, or whether it is a regular expression.

    Both at once, because they are one decision: turning the checkbox on
    usually means rewriting the text in the same breath, and saving them
    separately would mean a moment where the row says something nobody
    meant — `beef (lean)` read as an expression, matching nothing.

    The compiled cache is dropped afterwards. Without that the old text
    keeps matching until the process restarts, which is the kind of bug
    that looks like the save silently failed.
    """
    from recipes.models import ReferencePattern
    from recipes.references import forget

    row = get_object_or_404(ReferencePattern.objects.select_related("reference"), pk=pk)
    reference = row.reference

    form = ReferencePatternForm(request.POST, instance=row)
    if form.is_valid():
        form.save()
        forget()
        messages.success(request, f"“{row.pattern}” updated.")
    else:
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)

    return redirect(f"{reverse('pantry:references')}#reference-{reference.pk}")


@login_required
@require_POST
def reference_pattern_remove(request, pk):
    from recipes.models import ReferencePattern
    from recipes.references import forget

    row = get_object_or_404(ReferencePattern.objects.select_related("reference"), pk=pk)
    reference = row.reference
    row.delete()
    forget()

    if not reference.patterns.exists():
        messages.warning(
            request,
            f"{reference} has no phrasings left, so nothing will match it.",
        )
    else:
        messages.success(request, "Phrasing removed.")
    return redirect(f"{reverse('pantry:references')}#reference-{reference.pk}")


@login_required
@require_GET
def meal_food_search(request):
    """Portions, for the attach-a-portion picker on the reference page."""
    from django.db.models import Q

    from nutrition.models import MealFood

    term = " ".join(request.GET.get("q", "").split())
    if len(term) < 2:
        return JsonResponse({"results": []})

    found = MealFood.objects.filter(
        Q(source_food__name__icontains=term) | Q(source_meal__name__icontains=term)
    ).select_related("source_food", "source_meal")[:20]

    return JsonResponse(
        {
            "results": [
                {
                    "id": row.pk,
                    "name": str(row.source),
                    "description": getattr(row.source_food, "description", "") or "",
                }
                for row in found
            ]
        }
    )


@login_required
def reference_edit(request, pk):
    from recipes.models import Reference
    from recipes.references import forget

    reference = get_object_or_404(Reference, pk=pk)
    was = reference.pattern

    if request.method == "POST":
        if "delete" in request.POST:
            forget(was)
            reference.delete()
            messages.success(request, "Reference removed.")
            return redirect("pantry:references")

        form = ReferenceForm(request.POST, instance=reference)
        if form.is_valid():
            forget(was)  # the old pattern must not stay cached
            form.save()
            messages.success(request, "Reference updated.")
            return redirect("pantry:references")
        messages.error(request, "That pattern needs fixing.")
    else:
        form = ReferenceForm(instance=reference)

    return render(
        request,
        "pantry/reference_form.html",
        {"form": form, "reference": reference, "page_title": f"Edit {reference}"},
    )


@login_required
@require_POST
def reference_attach(request, pk):
    """Put a portion on a reference's quick-pick list, or take it off."""
    from nutrition.models import MealFood
    from recipes.models import FoodReference, Reference

    reference = get_object_or_404(Reference, pk=pk)

    detach = request.POST.get("detach")
    if detach:
        FoodReference.objects.filter(reference=reference, meal_food_id=detach).delete()
        messages.success(request, "Removed from the list.")
        return redirect("pantry:references")

    meal_food = get_object_or_404(MealFood, pk=request.POST.get("meal_food"))
    FoodReference.objects.get_or_create(
        reference=reference,
        meal_food=meal_food,
        defaults={"position": reference.food_references.count()},
    )
    messages.success(request, f"{meal_food} is now an option for {reference}.")
    return redirect("pantry:references")


@login_required
@require_GET
def reference_options(request, pk):
    """The quick-pick list for one reference, for the recipe page's dialog."""
    from recipes.models import Reference

    reference = get_object_or_404(
        Reference.objects.prefetch_related(
            "food_references__meal_food__source_food",
            "food_references__meal_food__source_meal",
        ),
        pk=pk,
    )
    return JsonResponse(
        {
            "reference": str(reference),
            "results": [
                {
                    "id": row.meal_food_id,
                    "name": str(row.meal_food.source),
                    "description": getattr(
                        row.meal_food.source_food, "description", ""
                    ) or "",
                }
                for row in reference.food_references.all()
            ],
        }
    )
