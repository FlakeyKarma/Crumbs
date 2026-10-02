from django.contrib import admin

from .models import (
    CoreMacro,
    Food,
    FoodPortion,
    FoodTracking,
    Macro,
    Meal,
    MealFood,
    Micro,
    MicroCategory,
    Unit,
)


class FoodPortionInline(admin.TabularInline):
    """What one clove, slice or can of this weighs."""

    model = FoodPortion
    extra = 0
    autocomplete_fields = ["unit"]


class MacroInline(admin.TabularInline):
    model = Macro
    extra = 0
    autocomplete_fields = ["core_macro", "unit"]


class MicroInline(admin.TabularInline):
    model = Micro
    extra = 0
    autocomplete_fields = ["category", "unit"]


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ["symbol", "name", "dimension", "to_base", "is_base"]
    list_filter = ["dimension"]
    search_fields = ["symbol", "name"]


@admin.register(CoreMacro)
class CoreMacroAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "position"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}


@admin.register(MicroCategory)
class MicroCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "position"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}


@admin.register(Food)
class FoodAdmin(admin.ModelAdmin):
    list_display = ["name", "reference", "density_g_per_ml", "macro_count", "micro_count"]
    list_filter = ["source_api"]
    search_fields = ["name", "description", "barcode", "external_id"]
    inlines = [FoodPortionInline, MacroInline, MicroInline]
    autocomplete_fields = ["reference_unit"]

    @admin.display(description="Per")
    def reference(self, obj):
        return f"{obj.reference_quantity:g} {obj.reference_unit.symbol}"

    @admin.display(description="Macros")
    def macro_count(self, obj):
        return obj.macros.count()

    @admin.display(description="Micros")
    def micro_count(self, obj):
        return obj.micros.count()


class MealFoodInline(admin.TabularInline):
    model = MealFood
    fk_name = "target_meal"
    extra = 1
    autocomplete_fields = ["source_food", "source_meal"]


@admin.register(Meal)
class MealAdmin(admin.ModelAdmin):
    list_display = ["name", "recipe", "part_count"]
    search_fields = ["name", "description"]
    inlines = [MealFoodInline]

    @admin.display(description="Parts")
    def part_count(self, obj):
        return obj.components.count()


@admin.register(MealFood)
class MealFoodAdmin(admin.ModelAdmin):
    list_display = ["__str__", "source_is_meal", "target_meal", "portion_percentage"]
    list_filter = ["source_is_meal"]
    autocomplete_fields = ["source_food", "source_meal", "target_meal"]
    # MealFood has no text of its own, so searching it means searching
    # whatever it points at. Required: FoodTrackingAdmin autocompletes on it.
    search_fields = ["source_food__name", "source_meal__name", "target_meal__name"]


@admin.register(FoodTracking)
class FoodTrackingAdmin(admin.ModelAdmin):
    list_display = ["timestamp", "user", "meal_food", "portion"]
    list_filter = ["user"]
    date_hierarchy = "timestamp"
    autocomplete_fields = ["meal_food"]
