"""Forms for the Health Panel."""

from decimal import Decimal

from django import forms

from .models import (
    Cadence,
    HealthSettings,
    HealthTarget,
    MealSlot,
    Metric,
    MetricKind,
    Observation,
    TargetBasis,
    TargetMode,
)


class TargetForm(forms.ModelForm):
    """Setting a target. Saving never edits the old one — see the view."""

    class Meta:
        model = HealthTarget
        fields = ["mode", "basis", "cadence", "lower", "upper", "effective_from", "note"]
        widgets = {
            "effective_from": forms.DateInput(attrs={"type": "date"}),
            "lower": forms.NumberInput(attrs={"step": "any"}),
            "upper": forms.NumberInput(attrs={"step": "any"}),
            "note": forms.TextInput(attrs={"placeholder": "why, so you remember later"}),
        }
        labels = {"lower": "At least", "upper": "At most", "effective_from": "From"}

    def __init__(self, *args, metric=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.metric = metric
        if metric and not metric.energy_per_gram:
            # Only a macro with a kcal-per-gram figure can be a share of energy.
            self.fields["basis"].choices = [
                (value, label)
                for value, label in TargetBasis.choices
                if value == TargetBasis.ABSOLUTE
            ]
            self.fields["basis"].help_text = (
                "This metric has no kcal-per-gram figure, so it can only be set "
                "in its own unit."
            )

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get("mode")
        lower, upper = cleaned.get("lower"), cleaned.get("upper")

        if mode == TargetMode.FLOOR and lower is None:
            self.add_error("lower", "An 'at least' target needs a lower figure.")
        if mode == TargetMode.CEILING and upper is None:
            self.add_error("upper", "An 'at most' target needs an upper figure.")
        if mode == TargetMode.RANGE:
            if lower is None or upper is None:
                self.add_error("upper", "A range needs both ends.")
            elif lower > upper:
                self.add_error("upper", "The upper figure is below the lower one.")
        return cleaned


class MetricForm(forms.ModelForm):
    """Adding a metric of your own. Observed only — see the help text."""

    class Meta:
        model = Metric
        fields = ["name", "slug", "unit", "kind", "aggregation", "decimals"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Resting heart rate", "autofocus": True}),
            "slug": forms.TextInput(attrs={"placeholder": "resting-heart-rate"}),
            "unit": forms.TextInput(attrs={"placeholder": "bpm"}),
        }
        help_texts = {
            "aggregation": "How a day's readings become one number. Totals add up; "
                           "a heart rate or a weight does not.",
            "kind": "Only grouping — it decides where the metric sits on the panel.",
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def save(self, commit=True):
        metric = super().save(commit=False)
        metric.owner = self.user
        # A metric that reads the food log has to name a real Pantry field,
        # so those are added through the admin, not from here.
        metric.nutrient_field = ""
        if commit:
            metric.save()
        return metric


class ObservationForm(forms.ModelForm):
    class Meta:
        model = Observation
        fields = ["metric", "value", "observed_at", "note"]
        widgets = {
            "observed_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "value": forms.NumberInput(attrs={"step": "any", "autofocus": True}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Derived metrics are read off the food log and must not be typeable,
        # or the same calories get counted twice.
        self.fields["metric"].queryset = Metric.objects.available_to(user).observed()


class QuickRecipeLogForm(forms.Form):
    servings = forms.DecimalField(
        min_value=Decimal("0.01"), initial=Decimal("1"), decimal_places=2, max_digits=6
    )
    slot = forms.ChoiceField(choices=MealSlot.choices, initial=MealSlot.DINNER, label="Meal")
    consumed_at = forms.DateTimeField(
        required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        label="When", help_text="Leave empty for now.",
    )
    deplete_stock = forms.BooleanField(
        required=False,
        label="Take the ingredients out of the pantry too",
        help_text="Leave off if you already recorded the stock when you cooked.",
    )


class HealthSettingsForm(forms.ModelForm):
    class Meta:
        model = HealthSettings
        fields = [
            "movement_metric",
            "offset_energy_with_movement",
            "offset_ratio",
            "weight_smoothing_days",
            "week_starts_monday",
            "show_metrics",
        ]
        labels = {
            "movement_metric": "Movement figure on the panel",
            "offset_energy_with_movement": "Add energy burned back to the day's calories",
            "offset_ratio": "How much of it to add back",
            "show_metrics": "Pinned to the top of the panel",
        }
        widgets = {"show_metrics": forms.CheckboxSelectMultiple}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        available = Metric.objects.available_to(user)
        self.fields["show_metrics"].queryset = available
        self.fields["movement_metric"].queryset = available.filter(kind=MetricKind.MOVEMENT)
        self.fields["movement_metric"].required = False


class MacroPlanForm(forms.ModelForm):
    """Which side you set, and how the other is worked out."""

    class Meta:
        from .models import MacroPlan

        model = MacroPlan
        fields = [
            "mode",
            "split",
            "energy_lower",
            "energy_upper",
            "protein_lower",
            "protein_upper",
            "carbohydrate_lower",
            "carbohydrate_upper",
            "fat_lower",
            "fat_upper",
        ]
        widgets = {
            "mode": forms.RadioSelect,
            "energy_lower": forms.NumberInput(attrs={"placeholder": "2000", "step": "any"}),
            "energy_upper": forms.NumberInput(attrs={"placeholder": "2400", "step": "any"}),
        }
        labels = {
            "energy_lower": "Calories, at least",
            "energy_upper": "Calories, at most",
            "split": "Split",
        }
        help_texts = {
            "split": "The percentages below are only used when this is set to your own.",
        }
