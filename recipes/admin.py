from django.contrib import admin

from .models import (
    Ingredient,
    Note,
    NoteShare,
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
