"""The Health Panel.

Targets for the things you're watching, measured against what the Pantry says
you ate.

The shape that makes this extensible is that **a metric is a row, not a
field**. Built-in metrics ship with no owner; a user metric is the same model
with one. Adding "resting heart rate" is a form submission, not a migration.

Two kinds of metric, and the difference decides everything else:

* **derived** — has a ``nutrient_field``, so it reads off the food log and
  cannot be typed in. Energy, protein, sodium.
* **observed** — no ``nutrient_field``, recorded by a person or a device.
  Weight, steps, sleep, mood.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class MetricKind(models.TextChoices):
    NUTRITION = "nutrition", "Nutrition"
    BODY = "body", "Body"
    MOVEMENT = "movement", "Movement"
    OTHER = "other", "Other"


class Aggregation(models.TextChoices):
    """How a day's readings become one number.

    The classic tracker bug lives here: calories add up over a day, body
    weight does not. Summing a weight produces a figure with no meaning, so
    every metric has to say which it is.
    """

    SUM = "sum", "Total for the day"
    LAST = "last", "Most recent reading"
    MEAN = "mean", "Average of the readings"
    MAX = "max", "Highest reading"
    MIN = "min", "Lowest reading"


class TargetMode(models.TextChoices):
    FLOOR = "floor", "At least"
    CEILING = "ceiling", "At most"
    RANGE = "range", "Between"
    TRACK = "track", "Track only, no target"


class TargetBasis(models.TextChoices):
    ABSOLUTE = "absolute", "In the metric's own unit"
    PERCENT_ENERGY = "percent_energy", "As a share of the energy target"


class Cadence(models.TextChoices):
    DAILY = "daily", "Each day"
    WEEKLY = "weekly", "Across the week"


class MetricQuerySet(models.QuerySet):
    def available_to(self, user):
        owned = Q(owner=user) if (user and user.is_authenticated) else Q(pk__in=[])
        return self.filter(Q(owner__isnull=True) | owned, is_active=True)

    def derived(self):
        return self.exclude(nutrient_field="")

    def observed(self):
        return self.filter(nutrient_field="")


class Metric(models.Model):
    """One measurable thing.

    A user metric may reuse a built-in slug, in which case it shadows the
    built-in for that person — which is how someone switches weight from kg
    to pounds without a migration landing on everybody.
    """

    slug = models.SlugField(max_length=64)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="health_metrics",
        help_text="Leave empty for a metric everyone can use.",
    )
    name = models.CharField(max_length=80)
    unit = models.CharField(max_length=24, blank=True, help_text="kcal, g, kg, steps…")
    kind = models.CharField(max_length=16, choices=MetricKind.choices, default=MetricKind.OTHER)
    aggregation = models.CharField(
        max_length=8, choices=Aggregation.choices, default=Aggregation.SUM
    )

    nutrient_field = models.CharField(
        max_length=64,
        blank=True,
        help_text=(
            "The field on a Pantry food this metric reads, such as energy_kcal. "
            "Leave empty for something the user records themselves."
        ),
    )
    energy_per_gram = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
        help_text="kcal per gram — 4 for protein and carbohydrate, 9 for fat. "
        "Needed to express this metric's target as a share of energy.",
    )
    decimals = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    position = models.PositiveSmallIntegerField(default=100)

    objects = MetricQuerySet.as_manager()

    class Meta:
        ordering = ["position", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["slug"], condition=Q(owner__isnull=True), name="unique_builtin_metric"
            ),
            models.UniqueConstraint(
                fields=["slug", "owner"],
                condition=Q(owner__isnull=False),
                name="unique_user_metric",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_derived(self):
        return bool(self.nutrient_field)

    def format(self, value):
        if value is None:
            return "—"
        quantised = Decimal(value).quantize(Decimal(1).scaleb(-self.decimals))
        return f"{quantised:,f}".rstrip("0").rstrip(".") if self.decimals else f"{quantised:,.0f}"


class HealthTarget(models.Model):
    """What you're aiming for, as of a date.

    Effective-dated rather than mutable: changing a target closes the old row
    and opens a new one, so a day in March is still measured against what was
    set in March. A tracker that rewrites its own history is not much use.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="health_targets"
    )
    metric = models.ForeignKey(Metric, on_delete=models.CASCADE, related_name="targets")

    mode = models.CharField(max_length=8, choices=TargetMode.choices, default=TargetMode.FLOOR)
    basis = models.CharField(
        max_length=16, choices=TargetBasis.choices, default=TargetBasis.ABSOLUTE
    )
    cadence = models.CharField(max_length=8, choices=Cadence.choices, default=Cadence.DAILY)

    lower = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    upper = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    effective_from = models.DateField(default=timezone.localdate)
    effective_to = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=140, blank=True)

    class Meta:
        ordering = ["metric__position", "-effective_from"]
        indexes = [models.Index(fields=["user", "metric", "effective_from"])]

    def __str__(self):
        return f"{self.metric}: {self.describe()}"

    def covers(self, day):
        if day < self.effective_from:
            return False
        return self.effective_to is None or day <= self.effective_to

    def describe(self):
        if self.mode == TargetMode.TRACK:
            return "tracked, no target"
        unit = f" {self.metric.unit}" if self.metric.unit else ""
        if self.mode == TargetMode.RANGE:
            return f"between {self.lower:g} and {self.upper:g}{unit}"
        if self.mode == TargetMode.CEILING:
            return f"at most {self.upper:g}{unit}"
        return f"at least {self.lower:g}{unit}"

    def resolved_bounds(self, energy_target=None):
        """Bounds in the metric's own unit.

        A macro set as a share of energy is stored as a percentage and turned
        into grams here, against the energy target in force on the same day —
        so raising the calorie goal moves the protein goal with it.
        """
        if self.basis != TargetBasis.PERCENT_ENERGY:
            return self.lower, self.upper
        if not energy_target or not self.metric.energy_per_gram:
            return None, None

        def grams(percent):
            if percent is None:
                return None
            return (Decimal(energy_target) * Decimal(percent) / Decimal(100)) / self.metric.energy_per_gram

        return grams(self.lower), grams(self.upper)


class MealSlot(models.TextChoices):
    BREAKFAST = "breakfast", "Breakfast"
    LUNCH = "lunch", "Lunch"
    DINNER = "dinner", "Dinner"
    SNACK = "snack", "Snack"


class ConsumptionEntry(models.Model):
    """Something a person ate.

    Either a recipe at some number of servings, or a food at some number of
    grams. Nutrition is frozen onto the row at save time rather than
    recomputed on read, because editing a recipe in June must not silently
    rewrite what you ate in March.

    The field is ``slot``, not ``meal`` — ``meal`` would collide with the
    cooked-dish sense used in the Pantry, and one of the two would have won
    by accident.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="consumption"
    )
    consumed_at = models.DateTimeField(default=timezone.now)
    slot = models.CharField(max_length=10, choices=MealSlot.choices, default=MealSlot.DINNER)

    recipe = models.ForeignKey(
        "recipes.Recipe", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    entry = models.ForeignKey(
        "pantry.FoodEntry", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    servings = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    grams = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    #: Frozen at save. Keys are Metric.nutrient_field names.
    nutrients = models.JSONField(default=dict, blank=True)
    incomplete = models.BooleanField(
        default=False,
        help_text="Some ingredient could not be converted, so these figures are a floor.",
    )
    label = models.CharField(max_length=140, blank=True)

    def nutrition_table(self):
        """The frozen nutrients, grouped the way the pantry groups them.

        Read off `self.nutrients`, not off the food: the whole point of
        freezing is that correcting a food's numbers next month leaves what
        you ate last month alone.
        """
        from pantry.models import FoodEntry, nutrient_unit

        groups = []
        for label, fields in FoodEntry.NUTRIENT_GROUPS:
            rows = [
                {
                    "field": field,
                    "label": FoodEntry._meta.get_field(field).verbose_name,
                    "value": self.nutrients.get(field),
                    "unit": nutrient_unit(field),
                }
                for field in fields
            ]
            groups.append(
                {
                    "label": label,
                    "rows": rows,
                    "recorded": [row for row in rows if row["value"] is not None],
                }
            )
        return groups

    class Meta:
        ordering = ["-consumed_at"]
        indexes = [models.Index(fields=["user", "consumed_at"])]
        verbose_name_plural = "consumption entries"

    def __str__(self):
        return self.label or (str(self.recipe or self.entry) or "Something")

    @property
    def day(self):
        return timezone.localtime(self.consumed_at).date()

    def value_of(self, nutrient_field):
        raw = self.nutrients.get(nutrient_field)
        return Decimal(str(raw)) if raw is not None else None


class ObservationSource(models.TextChoices):
    MANUAL = "manual", "Typed in"
    DEVICE = "device", "From a device"
    IMPORT = "import", "Imported"


class Observation(models.Model):
    """A reading of an observed metric: a weight, a step count, an hour slept."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="observations"
    )
    metric = models.ForeignKey(Metric, on_delete=models.CASCADE, related_name="observations")
    value = models.DecimalField(max_digits=12, decimal_places=3)
    observed_at = models.DateTimeField(default=timezone.now)
    source = models.CharField(
        max_length=8, choices=ObservationSource.choices, default=ObservationSource.MANUAL
    )
    note = models.CharField(max_length=140, blank=True)

    class Meta:
        ordering = ["-observed_at"]
        indexes = [models.Index(fields=["user", "metric", "observed_at"])]

    def __str__(self):
        return f"{self.metric}: {self.value}"


class HealthSettings(models.Model):
    """Per-user panel preferences."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="health_settings"
    )
    movement_metric = models.ForeignKey(
        Metric,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Which movement figure the panel leads with.",
    )
    offset_energy_with_movement = models.BooleanField(
        default=False,
        help_text="Add energy burned back to the day's calorie allowance.",
    )
    offset_ratio = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal("0.50"),
        help_text="How much of it to add back. Wearable burn estimates run high, "
        "and feeding a rough number straight into the allowance compounds the error.",
    )
    weight_smoothing_days = models.PositiveSmallIntegerField(
        default=7, help_text="Body weight is noisy day to day; the panel shows a rolling mean."
    )
    week_starts_monday = models.BooleanField(default=True)
    show_metrics = models.ManyToManyField(Metric, blank=True, related_name="pinned_by")

    class Meta:
        verbose_name_plural = "health settings"

    def __str__(self):
        return f"Health settings for {self.user}"

    @classmethod
    def load(cls, user):
        row, _ = cls.objects.get_or_create(user=user)
        return row


class PlanMode(models.TextChoices):
    """Which side of the arithmetic is the one you decided."""

    OFF = "off", "Set each target by hand"
    FROM_CALORIES = "calories", "Set calories, split them into macros"
    FROM_MACROS = "macros", "Set macros, add them up into calories"


class MacroSplit(models.TextChoices):
    MAINTENANCE = "maintenance", "General health and maintenance"
    FAT_LOSS = "fat-loss", "Fat loss"
    MUSCLE_GAIN = "muscle-gain", "Muscle gain"
    CUSTOM = "custom", "My own percentages"


#: slug -> ((protein low, high), (carbohydrate low, high), (fat low, high))
#:
#: The bands overlap and none of them sums to exactly 100 at either end —
#: that is how they are published, and narrowing them to make the arithmetic
#: tidy would be inventing precision. The lows sum under 100 and the highs
#: over it, which is what makes a range satisfiable at all.
SPLIT_PRESETS = {
    MacroSplit.MAINTENANCE: ((25, 30), (50, 55), (15, 25)),
    MacroSplit.FAT_LOSS: ((30, 35), (35, 40), (25, 30)),
    MacroSplit.MUSCLE_GAIN: ((30, 35), (40, 50), (15, 25)),
}


def percent_field(default):
    return models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal(default),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )


class MacroPlan(models.Model):
    """How calories and macros are kept in step.

    They are two views of one thing — grams times four, four and nine — so
    setting both by hand means maintaining two numbers that can disagree.
    This picks which one you decided and works the other out.

    Deliberately *not* both at once. Deriving calories from macros while
    deriving macros from calories is circular, and the version of this that
    tries to be clever about it ends up with targets that drift every time
    the page is loaded.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="macro_plan"
    )
    mode = models.CharField(max_length=10, choices=PlanMode.choices, default=PlanMode.OFF)
    split = models.CharField(
        max_length=14, choices=MacroSplit.choices, default=MacroSplit.MAINTENANCE
    )

    energy_lower = models.DecimalField(
        max_digits=8, decimal_places=1, null=True, blank=True,
        help_text="Used when calories are the thing you set.",
    )
    energy_upper = models.DecimalField(
        max_digits=8, decimal_places=1, null=True, blank=True
    )

    protein_lower = percent_field("25")
    protein_upper = percent_field("30")
    carbohydrate_lower = percent_field("50")
    carbohydrate_upper = percent_field("55")
    fat_lower = percent_field("15")
    fat_upper = percent_field("25")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "macro plan"
        verbose_name_plural = "macro plans"

    def __str__(self):
        return f"{self.get_mode_display()} for {self.user}"

    @property
    def percentages(self):
        """The split in use: the preset's, unless it is set to custom."""
        if self.split in SPLIT_PRESETS:
            return {
                macro: (Decimal(low), Decimal(high))
                for macro, (low, high) in zip(
                    ("protein", "carbohydrate", "fat"), SPLIT_PRESETS[self.split]
                )
            }
        return {
            macro: (getattr(self, f"{macro}_lower"), getattr(self, f"{macro}_upper"))
            for macro in ("protein", "carbohydrate", "fat")
        }

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.mode == PlanMode.FROM_CALORIES and not (
            self.energy_lower or self.energy_upper
        ):
            raise ValidationError(
                {"energy_lower": "Splitting calories into macros needs a calorie range."}
            )

        if self.energy_lower and self.energy_upper and self.energy_lower > self.energy_upper:
            raise ValidationError({"energy_upper": "The upper figure is below the lower one."})

        if self.split != MacroSplit.CUSTOM:
            return

        shares = self.percentages
        for macro, (low, high) in shares.items():
            if low > high:
                raise ValidationError(
                    {f"{macro}_upper": f"{macro.title()}'s upper share is below its lower one."}
                )

        # A range only works if 100% sits inside it. Lows summing over 100
        # cannot all be met; highs summing under 100 leave calories with
        # nowhere to go.
        lows = sum(low for low, _ in shares.values())
        highs = sum(high for _, high in shares.values())
        if lows > 100:
            raise ValidationError(
                f"The lowest shares add up to {lows}%, so they cannot all be met."
            )
        if highs < 100:
            raise ValidationError(
                f"The highest shares only add up to {highs}%, leaving calories unaccounted for."
            )

    @classmethod
    def for_user(cls, user):
        if not (user and user.is_authenticated):
            return None
        return cls.objects.filter(user=user).first()
