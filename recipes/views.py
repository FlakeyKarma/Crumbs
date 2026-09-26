"""Views for Crumbs.

Reading a recipe is the common case and stays fast and anonymous-friendly;
writing one is behind a login and belongs to whoever wrote it.
"""

import uuid
from decimal import Decimal
from functools import wraps

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, F
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import DeleteView, DetailView, ListView, TemplateView

from .appearance import COOKIE_MODE, COOKIE_THEME, clean_mode, clean_theme, cookie_kwargs
from .forms import (
    AppearanceForm,
    IngredientFormSet,
    NoteForm,
    NoteShareForm,
    RecipeForm,
    SiteSettingsForm,
    StepFormSet,
    ThemeForm,
    apply_order,
)
from .models import Ingredient, Note, NoteShare, Recipe, SiteSettings, Tag, Theme
from .theming import active_key, available_themes, resolve_theme

MAX_SERVINGS = 200
SORTS = {
    "recent": ("-created_at", "Recently added"),
    "title": ("title", "A to Z"),
    "quick": ("cook_minutes", "Quickest first"),
    "cooked": ("-times_cooked", "Made most often"),
}


class PublicPageMixin:
    """Honours the 'let signed-out visitors read shared recipes' setting.

    Applied to the three pages a visitor can reach without an account. With
    the setting off, the whole site needs a sign-in; with it on, nothing
    changes and `visible_to` still decides what each person sees.
    """

    def dispatch(self, request, *args, **kwargs):
        site = SiteSettings.load(request)
        if not request.user.is_authenticated and not site.allow_anonymous_browsing:
            return redirect_to_login(request.get_full_path())
        return super().dispatch(request, *args, **kwargs)


class RecipeListView(PublicPageMixin, ListView):
    """The index: everything the viewer is allowed to see, filtered down."""

    model = Recipe
    context_object_name = "recipes"
    template_name = "recipes/recipe_list.html"

    def get_paginate_by(self, queryset):
        return SiteSettings.load(self.request).recipes_per_page

    def get_queryset(self):
        queryset = Recipe.objects.visible_to(self.request.user).for_index()

        self.search_term = self.request.GET.get("q", "").strip()
        if self.search_term:
            queryset = queryset.search(self.search_term)

        self.tag = None
        tag_slug = self.kwargs.get("slug") or self.request.GET.get("tag")
        if tag_slug:
            self.tag = get_object_or_404(Tag, slug=tag_slug)
            queryset = queryset.filter(tags=self.tag)

        self.sort = self.request.GET.get("sort", "recent")
        if self.sort not in SORTS:
            self.sort = "recent"
        if self.sort == "quick":
            # A recipe with no stated cook time should not masquerade as quick.
            queryset = queryset.order_by(
                F("cook_minutes").asc(nulls_last=True), "-created_at"
            )
        else:
            queryset = queryset.order_by(SORTS[self.sort][0])
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search_term"] = self.search_term
        context["active_tag"] = self.tag
        context["sort"] = self.sort
        context["sorts"] = [(key, label) for key, (_, label) in SORTS.items()]
        context["popular_tags"] = (
            Tag.objects.filter(recipes__in=Recipe.objects.visible_to(self.request.user))
            .annotate(uses=Count("recipes", distinct=True))
            .order_by("-uses", "name")[:12]
        )
        return context


class TagListView(PublicPageMixin, ListView):
    model = Tag
    template_name = "recipes/tag_list.html"
    context_object_name = "tags"

    def get_queryset(self):
        visible = Recipe.objects.visible_to(self.request.user)
        return (
            Tag.objects.filter(recipes__in=visible)
            .annotate(uses=Count("recipes", distinct=True))
            .order_by("-uses", "name")
        )


class RecipeDetailView(PublicPageMixin, DetailView):
    """One recipe, scaled to whatever number of servings was asked for."""

    model = Recipe
    context_object_name = "recipe"
    template_name = "recipes/recipe_detail.html"

    def get_queryset(self):
        return (
            Recipe.objects.visible_to(self.request.user)
            .select_related("author")
            .prefetch_related("tags", "steps")
        )

    def requested_servings(self):
        raw = self.request.GET.get("servings")
        if raw is None:
            return self.object.servings
        try:
            servings = int(raw)
        except (TypeError, ValueError):
            return self.object.servings
        return max(1, min(servings, MAX_SERVINGS))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        recipe = self.object
        servings = self.requested_servings()
        scale = recipe.scale_factor(servings)

        context["servings"] = servings
        context["is_scaled"] = servings != recipe.servings
        context["scale_percent"] = int(Decimal(scale) * 100)
        context["fewer_servings"] = max(1, servings - 1)
        context["more_servings"] = min(MAX_SERVINGS, servings + 1)
        context["ingredient_groups"] = recipe.ingredient_groups(scale)
        context["can_edit"] = self.request.user.is_authenticated and (
            recipe.author_id == self.request.user.id or self.request.user.is_superuser
        )

        by_step, recipe_level, everything = notes_for(recipe, self.request.user)
        context["notes_by_step"] = by_step
        context["recipe_notes"] = recipe_level
        context["all_notes"] = everything
        context["cook_session"] = cook_session_token(self.request, recipe)
        return context


class RecipeEditorMixin(LoginRequiredMixin):
    """Shared plumbing for the create and edit pages.

    Deliberately not a ``ModelFormView``: the page saves one form and two
    formsets in a single transaction, which is clearer written out than bent
    into the generic editing views.
    """

    template_name = "recipes/recipe_form.html"

    def get_formsets(self, instance=None):
        kwargs = {"instance": instance}
        if self.request.method == "POST":
            kwargs["data"] = self.request.POST
        return (
            IngredientFormSet(prefix="ingredients", **kwargs),
            StepFormSet(prefix="steps", **kwargs),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        instance = getattr(self, "object", None)
        if "ingredient_formset" not in context:
            ingredients, steps = self.get_formsets(instance)
            context["ingredient_formset"] = ingredients
            context["step_formset"] = steps
        context["known_ingredients"] = Ingredient.objects.values_list("name", flat=True)[:500]
        return context


class RecipeCreateView(RecipeEditorMixin, TemplateView):
    page_title = "Add a recipe"

    def get(self, request, *args, **kwargs):
        self.object = None
        site = SiteSettings.load(request)
        form = RecipeForm(
            initial={
                "servings": site.default_servings,
                "is_shared": site.share_new_recipes,
            }
        )
        ingredients, steps = self.get_formsets()
        return self.render_to_response(
            self.get_context_data(
                form=form,
                ingredient_formset=ingredients,
                step_formset=steps,
                page_title=self.page_title,
            )
        )

    def post(self, request, *args, **kwargs):
        self.object = None
        form = RecipeForm(request.POST, request.FILES)
        ingredients, steps = self.get_formsets()

        # All three are validated before the branch, not short-circuited, so
        # a mistake in the title and a mistake in an ingredient row surface on
        # the same trip rather than one after the other.
        form_ok = form.is_valid()
        ingredients_ok = ingredients.is_valid()
        steps_ok = steps.is_valid()

        if form_ok and ingredients_ok and steps_ok:
            with transaction.atomic():
                recipe = form.save(commit=False)
                recipe.author = request.user
                recipe.save()
                form.save_tags(recipe)
                for formset in (ingredients, steps):
                    formset.instance = recipe
                    formset.save()
                    apply_order(formset)
            messages.success(request, f"Saved “{recipe.title}”.")
            return redirect(recipe)

        messages.error(request, "Something in the recipe needs fixing — see below.")
        return self.render_to_response(
            self.get_context_data(
                form=form,
                ingredient_formset=ingredients,
                step_formset=steps,
                page_title=self.page_title,
            )
        )


class RecipeUpdateView(RecipeEditorMixin, TemplateView):
    def get_recipe(self):
        recipe = get_object_or_404(Recipe, slug=self.kwargs["slug"])
        if recipe.author_id != self.request.user.id and not self.request.user.is_superuser:
            raise PermissionDenied("This recipe belongs to someone else.")
        return recipe

    def get(self, request, *args, **kwargs):
        self.object = self.get_recipe()
        form = RecipeForm(instance=self.object)
        ingredients, steps = self.get_formsets(self.object)
        return self.render_to_response(
            self.get_context_data(
                form=form,
                ingredient_formset=ingredients,
                step_formset=steps,
                recipe=self.object,
                page_title=f"Edit {self.object.title}",
            )
        )

    def post(self, request, *args, **kwargs):
        self.object = self.get_recipe()
        form = RecipeForm(request.POST, request.FILES, instance=self.object)
        ingredients, steps = self.get_formsets(self.object)

        # All three are validated before the branch, not short-circuited, so
        # a mistake in the title and a mistake in an ingredient row surface on
        # the same trip rather than one after the other.
        form_ok = form.is_valid()
        ingredients_ok = ingredients.is_valid()
        steps_ok = steps.is_valid()

        if form_ok and ingredients_ok and steps_ok:
            with transaction.atomic():
                recipe = form.save()
                for formset in (ingredients, steps):
                    formset.save()
                    apply_order(formset)
            messages.success(request, f"Updated “{recipe.title}”.")
            return redirect(recipe)

        messages.error(request, "Something in the recipe needs fixing — see below.")
        return self.render_to_response(
            self.get_context_data(
                form=form,
                ingredient_formset=ingredients,
                step_formset=steps,
                recipe=self.object,
                page_title=f"Edit {self.object.title}",
            )
        )


class RecipeDeleteView(LoginRequiredMixin, DeleteView):
    model = Recipe
    template_name = "recipes/recipe_confirm_delete.html"
    success_url = reverse_lazy("recipes:list")
    context_object_name = "recipe"

    def get_queryset(self):
        if self.request.user.is_superuser:
            return Recipe.objects.all()
        return Recipe.objects.filter(author=self.request.user)

    def form_valid(self, form):
        messages.success(self.request, f"Deleted “{self.object.title}”.")
        return super().form_valid(form)


@login_required
@require_POST
def mark_cooked(request, slug):
    """'I made this today.' One button, one row updated.

    Restricted to the recipe's owner: the count belongs to whoever keeps the
    recipe, and a signed-in visitor should not be able to move someone else's
    numbers.
    """
    queryset = Recipe.objects.all() if request.user.is_superuser else Recipe.objects.filter(author=request.user)
    recipe = get_object_or_404(queryset, slug=slug)
    recipe.mark_cooked(timezone.localdate())
    messages.success(request, f"Logged. You've made this {recipe.times_cooked} time(s).")
    return redirect(recipe)


# --- Settings menu ----------------------------------------------------------


def staff_only(view):
    """Send signed-out visitors to the login page, refuse everyone else.

    `user_passes_test` would bounce a signed-in non-staff user back to the
    login form they have already used, which reads as a broken site rather
    than as a refusal.
    """

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not request.user.is_staff:
            raise PermissionDenied("The settings menu is for administrators.")
        return view(request, *args, **kwargs)

    return wrapped


def theme_gallery():
    """Every profile, expanded, ready to show as a row of swatches."""
    themes = available_themes()
    return [resolve_theme(key, themes=themes) for key in sorted(themes)]


@staff_only
def settings_home(request):
    site = SiteSettings.load(request)

    if request.method == "POST":
        form = SiteSettingsForm(request.POST, instance=site)
        if form.is_valid():
            form.save()
            messages.success(request, "Settings saved.")
            return redirect("recipes:settings")
        messages.error(request, "Something in the settings needs fixing.")
    else:
        form = SiteSettingsForm(instance=site)

    return render(
        request,
        "recipes/settings.html",
        {
            "form": form,
            "site": site,
            "themes": theme_gallery(),
            "active_theme_key": active_key(),
            "settings_theme_default": getattr(django_settings, "CRUMBS_THEME", ""),
            "page_title": "Settings",
        },
    )


@staff_only
@require_POST
def set_default_appearance(request):
    """The theme a browser starts on before anyone has chosen for themselves."""
    form = AppearanceForm(request.POST, instance=SiteSettings.load(request))
    if form.is_valid():
        form.save()
        messages.success(request, "Default theme changed for new visitors.")
    else:
        messages.error(request, "That isn't a theme.")
    return redirect("recipes:settings")


@staff_only
@require_POST
def theme_activate(request, key):
    if key not in available_themes():
        raise Http404("No such theme.")
    site = SiteSettings.load()
    site.theme = key
    site.save()
    messages.success(request, f"Now using {resolve_theme(key)['name']}.")
    return redirect("recipes:settings")


@staff_only
def theme_create(request):
    """New theme, optionally starting from an existing one.

    `?from=<key>` pre-fills with that profile's expanded values, which is how
    "copy a built-in and tweak it" works: the built-in stays untouched in
    settings.py and the copy becomes an editable row.
    """
    initial = {}
    source_key = request.GET.get("from")
    if source_key and source_key in available_themes():
        source = resolve_theme(source_key)
        initial = {
            "name": f"{source['name']} (copy)",
            "key": f"{source_key}-copy",
            "description": source["description"],
            "roundness": source["roundness"],
            "primary": source["primary"],
            "secondary": source["secondary"],
        }

    if request.method == "POST":
        form = ThemeForm(request.POST)
        if form.is_valid():
            theme = form.save()
            messages.success(request, f"Created {theme.name}.")
            return redirect("recipes:settings")
        messages.error(request, "Something in the theme needs fixing.")
    else:
        form = ThemeForm(initial=initial)

    return render(
        request,
        "recipes/theme_form.html",
        {"form": form, "page_title": "New theme", "preview": resolve_theme()},
    )


@staff_only
def theme_edit(request, key):
    theme = get_object_or_404(Theme, key=key)

    if request.method == "POST":
        form = ThemeForm(request.POST, instance=theme)
        if form.is_valid():
            form.save()
            messages.success(request, f"Updated {theme.name}.")
            return redirect("recipes:settings")
        messages.error(request, "Something in the theme needs fixing.")
    else:
        form = ThemeForm(instance=theme)

    return render(
        request,
        "recipes/theme_form.html",
        {
            "form": form,
            "theme": theme,
            "page_title": f"Edit {theme.name}",
            "preview": resolve_theme(key),
        },
    )


@staff_only
def theme_delete(request, key):
    theme = get_object_or_404(Theme, key=key)

    if request.method == "POST":
        site = SiteSettings.load()
        name = theme.name
        theme.delete()
        if site.theme == key:
            # Don't leave the site pointing at a theme that no longer exists;
            # a shadowed built-in of the same key would quietly take over, and
            # anything else falls back to settings.CRUMBS_THEME.
            site.theme = key if key in available_themes() else ""
            site.save()
        messages.success(request, f"Deleted {name}.")
        return redirect("recipes:settings")

    return render(
        request,
        "recipes/theme_confirm_delete.html",
        {"theme": theme, "in_use": SiteSettings.load().theme == key},
    )


# --- Appearance -------------------------------------------------------------
#
# Two tiny endpoints. They are POSTs because they change state, they redirect
# back where you came from, and they work with JavaScript off — appearance.js
# only gets ahead of the round trip so the repaint feels instant.


def _back_to(request, fallback="recipes:list"):
    """Return to the page the control was on, without becoming an open redirect."""
    target = request.POST.get("next") or request.META.get("HTTP_REFERER") or ""
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(target)
    return redirect(fallback)


@require_POST
def set_theme(request):
    key = clean_theme(request.POST.get("theme"))
    if key is None:
        messages.error(request, "That isn't a theme.")
        return _back_to(request)
    response = _back_to(request)
    response.set_cookie(COOKIE_THEME, key, **cookie_kwargs())
    return response


@require_POST
def set_mode(request):
    mode = clean_mode(request.POST.get("mode"))
    if mode is None:
        return _back_to(request)
    response = _back_to(request)
    response.set_cookie(COOKIE_MODE, mode, **cookie_kwargs())
    return response


# --- Sticky notes -----------------------------------------------------------


def notes_for(recipe, user):
    """Every note on this recipe the viewer may read, bucketed by anchor.

    Returns ``(by_step, recipe_level, everything)``. Views need all three:
    icon and overlay modes place notes against their anchor, split mode shows
    the lot in one panel, and the pulse count needs the total.
    """
    notes = list(
        Note.objects.filter(recipe=recipe).visible_to(user).for_display()
    )
    for note in notes:
        # The template can't call editable_by(user), so resolve it here.
        note.editable_by_viewer = note.editable_by(user)

    by_step = {}
    recipe_level = []
    for note in notes:
        if note.anchor_step_id:
            by_step.setdefault(note.anchor_step_id, []).append(note)
        else:
            recipe_level.append(note)
    return by_step, recipe_level, notes


def cook_session_token(request, recipe):
    """A token that changes each time this recipe is opened afresh.

    Read state for notes hangs off this: same token, notes stay read; new
    token, everything pulses again. Stored in the Django session so a reload
    mid-cook doesn't reset it, and regenerated when the tab is closed and the
    recipe opened again.
    """
    key = f"cook:{recipe.pk}"
    token = request.session.get(key)
    if not token:
        token = uuid.uuid4().hex[:12]
        request.session[key] = token
    return token


@login_required
def note_create(request, slug):
    recipe = get_object_or_404(Recipe.objects.visible_to(request.user), slug=slug)

    if request.method == "POST":
        form = NoteForm(request.POST, request.FILES, recipe=recipe, author=request.user)
        if form.is_valid():
            note = form.save(commit=False)
            note.recipe = recipe
            note.author = request.user
            note.save()
            messages.success(request, "Note stuck on.")
            return redirect(recipe)
        messages.error(request, "Something in the note needs fixing.")
    else:
        form = NoteForm(recipe=recipe, author=request.user)

    return render(
        request,
        "recipes/note_form.html",
        {"form": form, "recipe": recipe, "page_title": f"Add a note to {recipe.title}"},
    )


@login_required
def note_edit(request, pk):
    note = get_object_or_404(Note.objects.select_related("recipe"), pk=pk)
    if not note.editable_by(request.user):
        raise PermissionDenied("That note belongs to someone else.")

    if request.method == "POST":
        form = NoteForm(
            request.POST, request.FILES, instance=note, recipe=note.recipe, author=request.user
        )
        if form.is_valid():
            form.save()
            messages.success(request, "Note updated.")
            return redirect(note.recipe)
        messages.error(request, "Something in the note needs fixing.")
    else:
        form = NoteForm(instance=note, recipe=note.recipe, author=request.user)

    return render(
        request,
        "recipes/note_form.html",
        {"form": form, "recipe": note.recipe, "note": note, "page_title": "Edit note"},
    )


@login_required
def note_delete(request, pk):
    note = get_object_or_404(Note.objects.select_related("recipe"), pk=pk)
    if not note.editable_by(request.user):
        raise PermissionDenied("That note belongs to someone else.")

    if request.method == "POST":
        recipe = note.recipe
        note.delete()
        messages.success(request, "Note peeled off.")
        return redirect(recipe)

    return render(request, "recipes/note_confirm_delete.html", {"note": note})


@login_required
def note_share(request, pk):
    note = get_object_or_404(Note.objects.select_related("recipe"), pk=pk)
    if not note.editable_by(request.user):
        raise PermissionDenied("That note belongs to someone else.")

    if request.method == "POST":
        if "remove" in request.POST:
            NoteShare.objects.filter(note=note, user_id=request.POST["remove"]).delete()
            if note.visibility == Note.Visibility.SHARED and not note.shares.exists():
                note.visibility = Note.Visibility.PRIVATE
                note.save(update_fields=["visibility", "updated_at"])
            messages.success(request, "Sharing removed.")
            return redirect("recipes:note-share", pk=note.pk)

        form = NoteShareForm(request.POST, note=note)
        if form.is_valid():
            NoteShare.objects.get_or_create(note=note, user=form.cleaned_data["username"])
            if note.visibility == Note.Visibility.PRIVATE:
                note.visibility = Note.Visibility.SHARED
                note.save(update_fields=["visibility", "updated_at"])
            messages.success(request, f"Shared with {form.cleaned_data['username']}.")
            return redirect("recipes:note-share", pk=note.pk)
    else:
        form = NoteShareForm(note=note)

    return render(
        request,
        "recipes/note_share.html",
        {"note": note, "form": form, "shares": note.shares.select_related("user")},
    )
