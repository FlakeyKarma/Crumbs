from django.contrib import admin

from .models import MacroPlan, ConsumptionEntry, HealthSettings, HealthTarget, Metric, Observation


@admin.register(Metric)
class MetricAdmin(admin.ModelAdmin):
    """Where derived metrics are added.

    A derived metric names a field on a Pantry food, so getting it wrong
    produces a metric that silently reads nothing — hence admin rather than
    the user-facing form.
    """

    list_display = ["name", "slug", "owner", "kind", "aggregation", "nutrient_field", "is_active"]
    list_filter = ["kind", "aggregation", "is_active"]
    search_fields = ["name", "slug", "nutrient_field"]
    prepopulated_fields = {"slug": ["name"]}


@admin.register(HealthTarget)
class HealthTargetAdmin(admin.ModelAdmin):
    list_display = ["user", "metric", "mode", "lower", "upper", "effective_from", "effective_to"]
    list_filter = ["mode", "cadence", "metric"]


@admin.register(ConsumptionEntry)
class ConsumptionEntryAdmin(admin.ModelAdmin):
    list_display = ["__str__", "user", "slot", "consumed_at", "incomplete"]
    list_filter = ["slot", "incomplete", "user"]
    readonly_fields = ["nutrients"]
    date_hierarchy = "consumed_at"


@admin.register(Observation)
class ObservationAdmin(admin.ModelAdmin):
    list_display = ["metric", "user", "value", "observed_at", "source"]
    list_filter = ["metric", "source"]
    date_hierarchy = "observed_at"


@admin.register(HealthSettings)
class HealthSettingsAdmin(admin.ModelAdmin):
    list_display = ["user", "movement_metric", "offset_energy_with_movement"]


@admin.register(MacroPlan)
class MacroPlanAdmin(admin.ModelAdmin):
    list_display = ["user", "mode", "split", "energy_lower", "energy_upper"]
    list_filter = ["mode", "split"]
    search_fields = ["user__username"]
