"""Health Panel views, plus the JSON the phone client logs against."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from pantry.models import FoodEntry, PantryItem
from recipes.models import Recipe

from . import services
from .forms import (
    HealthSettingsForm,
    MetricForm,
    ObservationForm,
    QuickRecipeLogForm,
    TargetForm,
)
from .models import ConsumptionEntry, HealthSettings, MealSlot, Metric


def _day_from(request):
    raw = request.GET.get("day")
    if not raw:
        return timezone.localdate()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise Http404("Not a date.")


def _payload(request):
    if request.content_type == "application/json" and request.body:
        try:
            return json.loads(request.body)
        except ValueError:
            return {}
    return request.POST.dict()


@login_required
def panel(request):
    day = _day_from(request)
    report = services.day_report(request.user, day)
    return render(
        request,
        "health/panel.html",
        {
            **report,
            "previous_day": day - timedelta(days=1),
            "next_day": day + timedelta(days=1),
            "is_today": day == timezone.localdate(),
            "trend": services.weight_trend(request.user),
            "leftovers": PantryItem.objects.filter(
                owner=request.user, quantity_g__gt=0, expires_on__isnull=False
            ).select_related("entry").order_by("expires_on")[:5],
            "page_title": "Health Panel",
        },
    )


@login_required
def targets(request):
    metrics = services.metrics_for(request.user)
    today = timezone.localdate()
    rows = [
        {"metric": metric, "target": services.target_on(request.user, metric, today)}
        for metric in metrics
    ]
    return render(
        request,
        "health/targets.html",
        {"rows": rows, "page_title": "Targets"},
    )


@login_required
def set_target(request, metric_id):
    """Close the old target and open a new one — never edit in place.

    Editing would rewrite how past days were scored, which makes the history
    a lie. Two rows cost nothing.
    """
    metric = get_object_or_404(Metric.objects.available_to(request.user), pk=metric_id)
    current = services.target_on(request.user, metric, timezone.localdate())

    if request.method == "POST":
        form = TargetForm(request.POST, metric=metric)
        if form.is_valid():
            target = form.save(commit=False)
            target.user = request.user
            target.metric = metric
            if current and current.effective_to is None:
                current.effective_to = target.effective_from - timedelta(days=1)
                current.save(update_fields=["effective_to"])
            target.save()
            messages.success(request, f"{metric}: {target.describe()}.")
            return redirect("health:targets")
        messages.error(request, "Something in the target needs fixing.")
    else:
        form = TargetForm(metric=metric, instance=None)

    return render(
        request,
        "health/target_form.html",
        {"form": form, "metric": metric, "current": current,
         "page_title": f"Target for {metric}"},
    )


@login_required
def add_metric(request):
    if request.method == "POST":
        form = MetricForm(request.POST, user=request.user)
        if form.is_valid():
            metric = form.save()
            messages.success(request, f"Added {metric}.")
            return redirect("health:targets")
    else:
        form = MetricForm(user=request.user)
    return render(
        request, "health/metric_form.html", {"form": form, "page_title": "Add a metric"}
    )


@login_required
def log_recipe(request, slug):
    recipe = get_object_or_404(Recipe.objects.visible_to(request.user), slug=slug)
    if request.method == "POST":
        form = QuickRecipeLogForm(request.POST)
        if form.is_valid():
            entry, problems = services.log_recipe(
                request.user,
                recipe,
                servings=form.cleaned_data["servings"],
                slot=form.cleaned_data["slot"],
                consumed_at=form.cleaned_data.get("consumed_at"),
                deplete_stock=form.cleaned_data["deplete_stock"],
            )
            if problems:
                messages.warning(
                    request,
                    f"Logged, but {len(problems)} ingredient(s) couldn't be converted, "
                    "so the figures are a floor.",
                )
            else:
                messages.success(request, f"Logged {recipe.title}.")
            return redirect("health:panel")
    else:
        form = QuickRecipeLogForm()
    return render(
        request,
        "health/log_form.html",
        {"form": form, "recipe": recipe, "page_title": f"Log {recipe.title}"},
    )


@login_required
@require_POST
def delete_entry(request, pk):
    entry = get_object_or_404(ConsumptionEntry, pk=pk, user=request.user)
    entry.delete()
    messages.success(request, "Removed from the log.")
    return redirect("health:panel")


@login_required
def add_observation(request):
    if request.method == "POST":
        form = ObservationForm(request.POST, user=request.user)
        if form.is_valid():
            observation = form.save(commit=False)
            observation.user = request.user
            observation.save()
            messages.success(request, f"Recorded {observation.metric}: {observation.value}.")
            return redirect("health:panel")
    else:
        form = ObservationForm(user=request.user)
    return render(
        request, "health/observation_form.html", {"form": form, "page_title": "Record a reading"}
    )


@login_required
def preferences(request):
    prefs = HealthSettings.load(request.user)
    if request.method == "POST":
        form = HealthSettingsForm(request.POST, instance=prefs, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Panel settings saved.")
            return redirect("health:panel")
    else:
        form = HealthSettingsForm(instance=prefs, user=request.user)
    return render(
        request, "health/preferences.html", {"form": form, "page_title": "Panel settings"}
    )


# --- JSON -------------------------------------------------------------------


@login_required
@require_GET
def api_day(request):
    report = services.day_report(request.user, _day_from(request))
    return JsonResponse(
        {
            "day": report["day"].isoformat(),
            "incomplete": report["incomplete"],
            "metrics": [
                {
                    "slug": row["metric"].slug,
                    "name": row["metric"].name,
                    "unit": row["metric"].unit,
                    "value": str(row["value"]) if row["value"] is not None else None,
                    "lower": str(row["lower"]) if row["lower"] is not None else None,
                    "upper": str(row["upper"]) if row["upper"] is not None else None,
                    "state": row["state"],
                }
                for row in report["rows"]
            ],
        }
    )


@login_required
@require_POST
def api_log(request):
    """``{"recipe": 12, "servings": 1.5, "slot": "dinner"}`` or
    ``{"entry": 40, "grams": 150}``."""
    data = _payload(request)
    slot = data.get("slot", MealSlot.DINNER)
    if slot not in MealSlot.values:
        slot = MealSlot.DINNER

    try:
        if data.get("recipe"):
            recipe = get_object_or_404(
                Recipe.objects.visible_to(request.user), pk=data["recipe"]
            )
            entry, problems = services.log_recipe(
                request.user,
                recipe,
                servings=Decimal(str(data.get("servings", 1))),
                slot=slot,
                deplete_stock=bool(data.get("deplete_stock")),
            )
            return JsonResponse(
                {"id": entry.pk, "label": entry.label, "problems": problems}, status=201
            )
        if data.get("entry"):
            food = get_object_or_404(FoodEntry, pk=data["entry"])
            entry = services.log_food(
                request.user,
                food,
                grams=Decimal(str(data.get("grams", 0))),
                slot=slot,
                deplete_stock=bool(data.get("deplete_stock", True)),
            )
            return JsonResponse({"id": entry.pk, "label": entry.label}, status=201)
    except (InvalidOperation, TypeError, ValueError):
        return JsonResponse({"error": "servings or grams was not a number"}, status=400)

    return JsonResponse({"error": "give a recipe or an entry"}, status=400)


@login_required
@require_POST
def api_observation(request):
    """``{"metric": "weight", "value": 82.1}``"""
    data = _payload(request)
    metric = (
        Metric.objects.available_to(request.user).observed().filter(slug=data.get("metric")).first()
    )
    if metric is None:
        return JsonResponse({"error": "unknown metric, or it is read from the food log"}, status=404)
    try:
        value = Decimal(str(data["value"]))
    except (KeyError, InvalidOperation, TypeError, ValueError):
        return JsonResponse({"error": "value is required and must be a number"}, status=400)

    from .models import ObservationSource

    observation = metric.observations.create(
        user=request.user, value=value, source=ObservationSource.DEVICE
    )
    return JsonResponse({"id": observation.pk, "metric": metric.slug, "value": str(value)}, status=201)


@login_required
def plan(request):
    """How calories and macros are kept in step.

    One page because it is one decision: which of the two you set, and how
    the other follows. Splitting it across a calories page and a macros page
    would invite setting both and wondering why they disagree.
    """
    from .forms import MacroPlanForm
    from .models import MacroPlan, SPLIT_PRESETS

    existing = MacroPlan.objects.filter(user=request.user).first()

    if request.method == "POST":
        form = MacroPlanForm(request.POST, instance=existing)
        if form.is_valid():
            saved = form.save(commit=False)
            saved.user = request.user
            saved.save()
            messages.success(request, "Plan saved.")
            return redirect("health:panel")
        messages.error(request, "Something in the plan needs fixing.")
    else:
        form = MacroPlanForm(instance=existing)

    return render(
        request,
        "health/plan.html",
        {
            "form": form,
            "plan": existing,
            # Shown as a table so the three published splits can be compared
            # before one is chosen, rather than picked blind from a dropdown.
            "presets": [
                {
                    "slug": slug,
                    "label": slug.label,
                    "shares": dict(zip(("protein", "carbohydrate", "fat"), shares)),
                }
                for slug, shares in SPLIT_PRESETS.items()
            ],
            "preview": services.plan_targets(request.user) if existing else {},
            "page_title": "Calories and macros",
        },
    )
