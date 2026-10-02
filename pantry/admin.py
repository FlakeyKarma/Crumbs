from django.contrib import admin

from .models import (
    FoodEntry,
    FoodPortion,
    FoodSource,
    IngredientDefault,
    PantryItem,
    StockMovement,
    UserAPICredential,
)


class FoodPortionInline(admin.TabularInline):
    model = FoodPortion
    extra = 1


class FoodSourceInline(admin.TabularInline):
    """Read-only: this is a log, and editing history defeats the point."""

    model = FoodSource
    extra = 0
    can_delete = False
    readonly_fields = ["api", "external_id", "fetched_at", "fetched_by", "raw_payload"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(FoodEntry)
class FoodEntryAdmin(admin.ModelAdmin):
    list_display = ["name", "brand", "source_api", "barcode", "energy_kcal", "is_active"]
    list_filter = ["source_api", "is_active"]
    search_fields = ["name", "brand", "barcode", "external_id"]
    inlines = [FoodPortionInline, FoodSourceInline]
    actions = ["retire", "restore"]

    @admin.action(description="Retire (hide from pickers, keep history intact)")
    def retire(self, request, queryset):
        queryset.update(is_active=False)

    @admin.action(description="Restore")
    def restore(self, request, queryset):
        queryset.update(is_active=True)

    def get_queryset(self, request):
        return FoodEntry.all_objects.all()


class StockMovementInline(admin.TabularInline):
    model = StockMovement
    extra = 0
    readonly_fields = ["kind", "grams", "actor", "recipe", "happened_at", "note"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PantryItem)
class PantryItemAdmin(admin.ModelAdmin):
    list_display = ["entry", "owner", "quantity_g", "expires_on", "location"]
    list_filter = ["owner", "location"]
    search_fields = ["entry__name", "note"]
    inlines = [StockMovementInline]


@admin.register(IngredientDefault)
class IngredientDefaultAdmin(admin.ModelAdmin):
    list_display = ["ingredient", "entry", "user"]
    list_filter = ["user"]
    search_fields = ["ingredient__name", "entry__name"]


@admin.register(UserAPICredential)
class UserAPICredentialAdmin(admin.ModelAdmin):
    """The key itself is never shown — it is encrypted and has no business here."""

    list_display = ["user", "api", "created_at", "last_used_at"]
    fields = ["user", "api", "created_at", "last_used_at"]
    readonly_fields = ["created_at", "last_used_at"]

    def has_add_permission(self, request):
        return False
