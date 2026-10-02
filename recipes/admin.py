from django.contrib import admin

from .models import (
    Ingredient,
    Note,
    NoteShare,
    FoodReference,
    Reference,
    ReferencePattern,
    UserAppearance,
    UserFoodReference,
    Recipe,
    RecipeIngredient,
    SiteSettings,
    Step,
    Tag,
    Theme,
)


class RecipeIngredientInline(admin.TabularInline):
    model = RecipeIngredient
    extra = 1
    autocomplete_fields = ["ingredient"]
    fields = ["position", "quantity", "unit", "ingredient", "preparation", "group", "is_optional"]


class StepInline(admin.TabularInline):
    model = Step
    extra = 1
    fields = ["position", "text", "minutes"]


@admin.register(Step)
class StepAdmin(admin.ModelAdmin):
    """Registered mainly so a note's anchor can be an autocomplete."""

    list_display = ["__str__", "recipe", "position"]
    search_fields = ["text", "recipe__title"]


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ["title", "author", "difficulty", "total_minutes", "times_cooked", "is_shared"]
    list_filter = ["is_shared", "difficulty", "tags", "author"]
    search_fields = ["title", "summary", "notes", "recipe_ingredients__ingredient__name"]
    prepopulated_fields = {"slug": ["title"]}
    filter_horizontal = ["tags"]
    inlines = [RecipeIngredientInline, StepInline]
    readonly_fields = ["created_at", "updated_at"]
    date_hierarchy = "created_at"

    @admin.display(description="Total time")
    def total_minutes(self, obj):
        return obj.total_minutes or "—"


@admin.register(Ingredient)
class IngredientAdmin(admin.ModelAdmin):
    list_display = ["name", "recipe_count"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ["name"]}

    @admin.display(description="Used in")
    def recipe_count(self, obj):
        return obj.used_in.count()


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ["name", "recipe_count"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ["name"]}

    @admin.display(description="Recipes")
    def recipe_count(self, obj):
        return obj.recipes.count()


@admin.register(Theme)
class ThemeAdmin(admin.ModelAdmin):
    list_display = ["name", "key", "primary", "secondary", "roundness"]
    search_fields = ["name", "key", "description"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = [
        (None, {"fields": ["name", "key", "description"]}),
        ("Colours", {"fields": ["roundness", "primary", "secondary"]}),
        (
            "Overrides",
            {
                "classes": ["collapse"],
                "description": "Leave blank to have these worked out from the two colours above.",
                "fields": list(Theme.OVERRIDES),
            },
        ),
        ("Dates", {"fields": ["created_at", "updated_at"]}),
    ]


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """Editable here too, though the settings menu is the nicer way in."""

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class NoteShareInline(admin.TabularInline):
    model = NoteShare
    extra = 0
    autocomplete_fields = ["user"]


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ["summary", "recipe", "author", "visibility", "anchor_step"]
    list_filter = ["visibility", "size", "icon"]
    search_fields = ["body", "recipe__title", "author__username"]
    autocomplete_fields = ["recipe", "anchor_step"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [NoteShareInline]


class ReferencePatternInline(admin.TabularInline):
    model = ReferencePattern
    extra = 1


class FoodReferenceInline(admin.TabularInline):
    model = FoodReference
    extra = 1
    autocomplete_fields = ["meal_food"]


@admin.register(Reference)
class ReferenceAdmin(admin.ModelAdmin):
    list_display = ["__str__", "phrasings", "option_count", "chosen_by"]
    search_fields = ["label", "patterns__pattern"]
    inlines = [ReferencePatternInline, FoodReferenceInline]

    @admin.display(description="Phrasings")
    def phrasings(self, obj):
        return ", ".join(obj.patterns.values_list("pattern", flat=True)) or "—"

    @admin.display(description="Options")
    def option_count(self, obj):
        return obj.food_references.count()

    @admin.display(description="Chosen by")
    def chosen_by(self, obj):
        return obj.choices.count()


@admin.register(UserFoodReference)
class UserFoodReferenceAdmin(admin.ModelAdmin):
    list_display = ["user", "recipe", "reference", "meal_food"]
    list_filter = ["user"]
    search_fields = ["reference__label", "recipe__title"]
    autocomplete_fields = ["recipe", "reference", "meal_food"]


@admin.register(UserAppearance)
class UserAppearanceAdmin(admin.ModelAdmin):
    list_display = ["user", "reference_foreground", "reference_background"]
    search_fields = ["user__username"]
