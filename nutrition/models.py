"""The normalised nutrition schema.

Replaces one wide FoodEntry table — 27 nutrient columns, each one a schema
change to add — with rows. Adding vitamin K here is an INSERT, not a
migration, which is the whole point of the shape.

Three deliberate departures from the table list this was built from, each
because the literal version could not hold the data:

1. **Macro and Micro point at Food, not the other way round.** As specified,
   `Food.macro_table_id` was a single foreign key, so a food could have
   exactly one macro and one micro. Since the rows are created per food, the
   key belongs on the row.

2. **`unit_count` is a decimal, not an integer.** 4.2 mg of iron rounds to 4
   and 0.75 µg of B12 rounds to 1 — the figures this table exists to hold
   are mostly fractional. (Integer counts of micrograms would also have
   worked, the way money is held in cents; decimal was the smaller change.)

3. **Unit and a reference quantity were added.** `unit_id` referred to a
   table that did not exist, and nothing said what quantity of food the
   counts describe — 4.2 mg of iron per *what*. Food carries a reference
   quantity, defaulting to 100 g, which is what both importers produce.

The polymorphic source on MealFood is two nullable keys plus a constraint
rather than a bare `(bool, int)` pair, so the database can still tell you
when a row points at nothing.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

#: How deep a meal may nest before we assume something is wrong. Meals can
#: contain meals, which is useful (a curry contains a spice paste) and also
#: the shape of an infinite loop.
MAX_MEAL_DEPTH = 12


class Dimension(models.TextChoices):
    """What a unit measures. Conversion only ever happens within one."""

    MASS = "mass", "Mass"
    VOLUME = "volume", "Volume"
    ENERGY = "energy", "Energy"
    COUNT = "count", "Count"


class Unit(models.Model):
    """A unit, and what it is worth in its dimension's base unit.

    Base units are gram, millilitre and kilocalorie. Everything converts
    through them, so a food recorded in micrograms and a target set in
    milligrams compare without either side knowing about the other.
    """

    name = models.CharField(max_length=40, unique=True)
    symbol = models.CharField(max_length=12, unique=True)
    dimension = models.CharField(max_length=10, choices=Dimension.choices)
    to_base = models.DecimalField(
        max_digits=20,
        decimal_places=10,
        default=Decimal("1"),
        help_text="How many base units one of these is worth. 1 for the base unit itself.",
    )

    class Meta:
        ordering = ["dimension", "-to_base"]

    def __str__(self):
        return self.symbol

    @property
    def is_base(self):
        return self.to_base == Decimal("1")

    def to_base_units(self, count):
        """This many of this unit, expressed in the dimension's base unit."""
        if count is None:
            return None
        return Decimal(count) * self.to_base

    def convert(self, count, target):
        """Convert into ``target``, which must measure the same thing."""
        if count is None:
            return None
        if target.dimension != self.dimension:
            raise ValidationError(
                f"Cannot convert {self.symbol} to {target.symbol}: "
                f"{self.get_dimension_display().lower()} is not "
                f"{target.get_dimension_display().lower()}."
            )
        return self.to_base_units(count) / target.to_base


class CoreMacro(models.Model):
    """The handful of things everything else is a component of.

    Energy, protein, carbohydrate, fat, fibre. A food's saturated fat is a
    component of its fat; its sugars are a component of its carbohydrate.
    """

    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=60, unique=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "name"]

    def __str__(self):
        return self.name


class MicroCategory(models.Model):
    """Minerals, vitamins, and whatever else gets tracked later."""

    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=60, unique=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "name"]
        verbose_name_plural = "micro categories"

    def __str__(self):
        return self.name


class Food(models.Model):
    """One food, and the quantity of it that its rows describe."""

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    reference_quantity = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        default=Decimal("100"),
        validators=[MinValueValidator(Decimal("0.001"))],
        help_text="The amount of food the macro and micro rows describe.",
    )
    reference_unit = models.ForeignKey(
        Unit,
        on_delete=models.PROTECT,
        related_name="foods_measured_in",
        help_text="Normally grams. Both importers give figures per 100 g.",
    )

    # Where it came from, for foods pulled out of FoodData Central or Open
    # Food Facts rather than typed in.
    source_api = models.CharField(max_length=20, blank=True)
    external_id = models.CharField(max_length=64, blank=True)
    barcode = models.CharField(max_length=32, blank=True, db_index=True)

    density_g_per_ml = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
        help_text=(
            "Needed to turn a volume in a recipe into a weight. Water is 1; "
            "oil about 0.92; flour about 0.53 loose."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_api", "external_id"],
                condition=~models.Q(external_id=""),
                # Project-wide namespace: pantry.FoodEntry already has a
                # constraint called unique_food_per_source.
                name="unique_nutrition_food_per_source",
            )
        ]

    def __str__(self):
        return self.name


class FoodPortion(models.Model):
    """What one of something weighs.

    A recipe saying "1 clove" or "2 slices" is counting, and counting means
    nothing to a table of figures per 100 g until somebody says what one of
    them weighs. Without a row here, a count is reported as a gap rather
    than guessed at — a plausible invented weight is worse than a blank,
    because it looks like an answer.
    """

    food = models.ForeignKey(Food, on_delete=models.CASCADE, related_name="portions_of")
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="+")
    grams = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    note = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["unit__symbol"]
        constraints = [
            models.UniqueConstraint(
                fields=["food", "unit"], name="unique_portion_per_food_unit"
            )
        ]

    def __str__(self):
        return f"1 {self.unit.symbol} of {self.food} = {self.grams:g} g"


class NutrientRow(models.Model):
    """Shared behaviour of a macro row and a micro row.

    Both are the same thing — a named quantity belonging to a food — and
    differ only in what they are grouped under.
    """

    food = models.ForeignKey(Food, on_delete=models.CASCADE, related_name="%(class)ss")
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="+")
    unit_count = models.DecimalField(
        max_digits=14,
        decimal_places=6,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Decimal, not a whole number: iron is 4.2 mg, not 4.",
    )
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        abstract = True
        ordering = ["position", "pk"]

    @property
    def in_base_units(self):
        return self.unit.to_base_units(self.unit_count)


class Macro(NutrientRow):
    """One component of one core macro, for one food."""

    core_macro = models.ForeignKey(
        CoreMacro, on_delete=models.PROTECT, related_name="components"
    )
    component_name = models.CharField(
        max_length=80,
        help_text="'Saturated fat', 'Sugars'. The core macro says what it is part of.",
    )

    class Meta(NutrientRow.Meta):
        abstract = False
        ordering = ["core_macro__position", "position", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["food", "core_macro", "component_name"],
                name="unique_macro_component_per_food",
            )
        ]

    def __str__(self):
        return f"{self.component_name} {self.unit_count} {self.unit.symbol}"


class Micro(NutrientRow):
    """One micronutrient, for one food."""

    category = models.ForeignKey(
        MicroCategory, on_delete=models.PROTECT, related_name="micros"
    )
    micro_name = models.CharField(max_length=80)

    class Meta(NutrientRow.Meta):
        abstract = False
        ordering = ["category__position", "position", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["food", "category", "micro_name"],
                name="unique_micro_per_food",
            )
        ]

    def __str__(self):
        return f"{self.micro_name} {self.unit_count} {self.unit.symbol}"


class Meal(models.Model):
    """Something assembled out of foods, and out of other meals."""

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    recipe = models.ForeignKey(
        "recipes.Recipe",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="meals",
        help_text="The recipe this was built from, if it came from one.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class MealFood(models.Model):
    """One portion of one food or meal — free-standing, or part of a meal.

    The centre of the model: anything anyone eats is one of these. With
    ``target_meal`` set it is an ingredient of that meal; with it null it
    stands alone and can be eaten directly.

    The source is two nullable keys and a constraint rather than the
    specified ``(is_meal, id)`` pair. ``is_meal`` is kept, because it is
    useful to read, but the database enforces that it agrees with whichever
    key is filled — a bare integer pointing at the wrong table is a class of
    bug that only shows up as missing food months later.
    """

    source_is_meal = models.BooleanField(
        default=False, help_text="False for a food, True for a meal."
    )
    source_food = models.ForeignKey(
        Food, on_delete=models.CASCADE, null=True, blank=True, related_name="portions"
    )
    source_meal = models.ForeignKey(
        Meal, on_delete=models.CASCADE, null=True, blank=True, related_name="portions"
    )

    target_meal = models.ForeignKey(
        Meal,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="components",
        help_text="The meal this is part of. Empty means it stands alone.",
    )

    portion_percentage = models.DecimalField(
        max_digits=9,
        decimal_places=4,
        default=Decimal("100"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Percent of the source. 100 is all of it; 50 is half.",
    )

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(source_is_meal=False, source_food__isnull=False, source_meal__isnull=True)
                    | models.Q(source_is_meal=True, source_meal__isnull=False, source_food__isnull=True)
                ),
                name="mealfood_source_matches_its_flag",
            ),
            models.CheckConstraint(
                # A meal cannot be an ingredient of itself. Deeper cycles
                # need a walk, which `clean` does.
                condition=~models.Q(source_meal=models.F("target_meal")),
                name="mealfood_no_self_reference",
            ),
        ]

    def __str__(self):
        return f"{self.portion_percentage}% of {self.source}"

    @property
    def source(self):
        return self.source_meal if self.source_is_meal else self.source_food

    @property
    def fraction(self):
        """The portion as a multiplier: 50% -> 0.5."""
        return Decimal(self.portion_percentage) / Decimal("100")

    def clean(self):
        source = self.source_meal if self.source_is_meal else self.source_food
        if source is None:
            raise ValidationError(
                {"source_is_meal": "Pick a source food or a source meal to match the flag."}
            )
        if self.source_is_meal and self.source_food_id:
            raise ValidationError({"source_food": "This row says its source is a meal."})
        if not self.source_is_meal and self.source_meal_id:
            raise ValidationError({"source_meal": "This row says its source is a food."})

        if self.source_is_meal and self.target_meal_id:
            self._reject_cycles()

    def _reject_cycles(self):
        """Refuse a meal that would eventually contain itself.

        A check constraint catches the one-step case. This walks the rest,
        because "curry contains paste contains curry" is just as fatal and
        the database has no way to see it.
        """
        wanted = self.target_meal_id
        seen = set()
        frontier = [self.source_meal_id]
        depth = 0

        while frontier:
            depth += 1
            if depth > MAX_MEAL_DEPTH:
                raise ValidationError("These meals nest too deeply to be sensible.")
            if wanted in frontier:
                raise ValidationError(
                    {"source_meal": "That would make this meal contain itself."}
                )
            seen.update(frontier)
            frontier = list(
                MealFood.objects.filter(target_meal_id__in=frontier, source_is_meal=True)
                .exclude(source_meal_id__in=seen)
                .values_list("source_meal_id", flat=True)
            )


class FoodTracking(models.Model):
    """What somebody ate, and when.

    Points at a MealFood rather than at a food or a meal, so one row covers
    both: half a bowl of yesterday's curry and a single apple are the same
    kind of record.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="food_tracking"
    )
    meal_food = models.ForeignKey(
        MealFood, on_delete=models.PROTECT, related_name="tracked"
    )
    timestamp = models.DateTimeField(db_index=True)
    portion = models.DecimalField(
        max_digits=9,
        decimal_places=4,
        default=Decimal("1"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="How much of that portion was eaten. 1 is all of it.",
    )

    class Meta:
        ordering = ["-timestamp"]
        indexes = [models.Index(fields=["user", "-timestamp"])]
        verbose_name_plural = "food tracking"

    def __str__(self):
        return f"{self.meal_food.source} at {self.timestamp:%Y-%m-%d %H:%M}"
