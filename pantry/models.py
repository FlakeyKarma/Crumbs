"""The Pantry: what a food is, and what you have in the house.

Two halves that are easy to confuse:

* the **catalogue** — ``FoodEntry`` and friends. Global to the installation,
  one row per food per source, all nutrients per 100 g. Imported from USDA
  FoodData Central and Open Food Facts, or typed in by hand.
* the **stock** — ``PantryItem`` lots and the ``StockMovement`` ledger. Per
  user, and the reason a recipe search can ask "what can I actually cook".

``FoodEntry`` is deliberately *not* the recipes app's ``Ingredient``. That one
is a name a recipe line points at ("smoked paprika"); this one is a specific
food with numbers on it, and there are usually several candidates for the same
name. ``IngredientDefault`` is the bridge: per user, when a recipe says X, use
this entry.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from django.urls import reverse
from django.utils import timezone

from recipes.models import Ingredient, Unit

from .fields import EncryptedTextField


class SourceAPI(models.TextChoices):
    FDC = "FDC", "USDA FoodData Central"
    OFF = "OFF", "Open Food Facts"
    MANUAL = "MANUAL", "Entered by hand"


def nutrient_field(**kwargs):
    return models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True, **kwargs)


class ActiveFoodManager(models.Manager):
    """Hides retired entries. What pickers and search should use."""

    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)


#: Unit suffix -> how to print it. The field name carries its own unit, so a
#: value can never drift away from the unit it was entered in.
UNIT_SUFFIXES = {"_kcal": "kcal", "_ug": "\u00b5g", "_mg": "mg", "_g": "g"}


def nutrient_unit(field):
    for suffix, label in UNIT_SUFFIXES.items():
        if field.endswith(suffix):
            return label
    return ""


class FoodEntry(models.Model):
    """One food, global to the installation, all nutrients per 100 g.

    Per-100 g because it makes scaling one multiplication, and because both
    sources can be normalised to it. The label serving is kept separately
    rather than being the storage basis — serving sizes are marketing, and
    converting at read time is where the bugs live.

    Every nutrient is nullable and every one means the same thing when null:
    *nobody has said*. Not zero. A food with no recorded iron is not a food
    with no iron, and the difference matters the moment anyone totals a day.
    """

    MACRONUTRIENTS = (
        "energy_kcal",
        "protein_g",
        "carbohydrate_g",
        "fat_g",
        "saturated_fat_g",
        "trans_fat_g",
        "sugars_g",
        "fibre_g",
        "cholesterol_mg",
    )

    MINERALS = (
        "sodium_mg",
        "potassium_mg",
        "calcium_mg",
        "iron_mg",
        "magnesium_mg",
        "zinc_mg",
        "phosphorus_mg",
    )

    VITAMINS = (
        "vitamin_a_ug",
        "vitamin_c_mg",
        "vitamin_d_ug",
        "vitamin_e_mg",
        "vitamin_k_ug",
        "thiamin_mg",
        "riboflavin_mg",
        "niacin_mg",
        "vitamin_b6_mg",
        "folate_ug",
        "vitamin_b12_ug",
    )

    #: Order matters: this is the order the form and the detail page use.
    NUTRIENT_GROUPS = (
        ("Macronutrients", MACRONUTRIENTS),
        ("Minerals", MINERALS),
        ("Vitamins", VITAMINS),
    )

    NUTRIENTS = MACRONUTRIENTS + MINERALS + VITAMINS

    name = models.CharField(max_length=255)
    brand = models.CharField(max_length=255, blank=True)
    barcode = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text="GTIN, UPC or EAN as the source gave it. Blank for generic foods.",
    )

    source_api = models.CharField(
        max_length=16, choices=SourceAPI.choices, default=SourceAPI.MANUAL
    )
    external_id = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        help_text="fdcId for FDC, product code for OFF. Empty for hand-entered foods.",
    )

    # Macronutrients
    energy_kcal = nutrient_field(verbose_name="energy (kcal)")
    protein_g = nutrient_field(verbose_name="protein (g)")
    carbohydrate_g = nutrient_field(verbose_name="carbohydrate (g)")
    fat_g = nutrient_field(verbose_name="fat (g)")
    saturated_fat_g = nutrient_field(verbose_name="saturated fat (g)")
    trans_fat_g = nutrient_field(verbose_name="trans fat (g)")
    sugars_g = nutrient_field(verbose_name="sugars (g)")
    fibre_g = nutrient_field(verbose_name="fibre (g)")
    cholesterol_mg = nutrient_field(verbose_name="cholesterol (mg)")

    # Minerals
    sodium_mg = nutrient_field(verbose_name="sodium (mg)")
    potassium_mg = nutrient_field(verbose_name="potassium (mg)")
    calcium_mg = nutrient_field(verbose_name="calcium (mg)")
    iron_mg = nutrient_field(verbose_name="iron (mg)")
    magnesium_mg = nutrient_field(verbose_name="magnesium (mg)")
    zinc_mg = nutrient_field(verbose_name="zinc (mg)")
    phosphorus_mg = nutrient_field(verbose_name="phosphorus (mg)")

    # Vitamins
    vitamin_a_ug = nutrient_field(verbose_name="vitamin A (\u00b5g RAE)")
    vitamin_c_mg = nutrient_field(verbose_name="vitamin C (mg)")
    vitamin_d_ug = nutrient_field(verbose_name="vitamin D (\u00b5g)")
    vitamin_e_mg = nutrient_field(verbose_name="vitamin E (mg)")
    vitamin_k_ug = nutrient_field(verbose_name="vitamin K (\u00b5g)")
    thiamin_mg = nutrient_field(verbose_name="thiamin, B1 (mg)")
    riboflavin_mg = nutrient_field(verbose_name="riboflavin, B2 (mg)")
    niacin_mg = nutrient_field(verbose_name="niacin, B3 (mg)")
    vitamin_b6_mg = nutrient_field(verbose_name="vitamin B6 (mg)")
    folate_ug = nutrient_field(verbose_name="folate (\u00b5g)")
    vitamin_b12_ug = nutrient_field(verbose_name="vitamin B12 (\u00b5g)")

    image = models.ImageField(
        upload_to="foods/%Y/%m/",
        blank=True,
        help_text="A photo of the packet or the food itself.",
    )

    serving_size = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    serving_unit = models.CharField(max_length=32, blank=True)

    density_g_per_ml = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Needed to measure this food by volume. Water is 1.0, oil ~0.92, flour ~0.53.",
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    #: Retired entries are hidden from pickers but must stay readable, or
    #: deactivating a food would rewrite everyone's logged history.
    objects = ActiveFoodManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name_plural = "food entries"
        ordering = ["name", "brand"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_api", "external_id"],
                condition=Q(external_id__isnull=False),
                name="unique_food_per_source",
            )
        ]
        indexes = [models.Index(fields=["name"]), models.Index(fields=["barcode"])]

    def __str__(self):
        return f"{self.name} ({self.brand})" if self.brand else self.name

    def get_absolute_url(self):
        return reverse("pantry:entry", args=[self.pk])

    @property
    def has_macros(self):
        return any(
            getattr(self, field) is not None
            for field in ("energy_kcal", "protein_g", "carbohydrate_g", "fat_g")
        )

    @property
    def fingerprint(self):
        """Groups the same product fetched from both sources into one choice."""
        return (
            self.barcode or "",
            tuple(
                int(getattr(self, f)) if getattr(self, f) is not None else None
                for f in ("energy_kcal", "protein_g", "carbohydrate_g", "fat_g")
            ),
        )

    def portion_map(self):
        """``{unit: grams}`` for this food, for the units converter."""
        return {p.unit: p.grams for p in self.portions.all()}

    def nutrients_for(self, grams):
        """Every stored nutrient scaled to ``grams``. ``None`` stays ``None``."""
        from .modules.units import scale_per_100g

        return {
            field: scale_per_100g(getattr(self, field), grams) for field in self.NUTRIENTS
        }

    def nutrition_table(self, grams=100):
        """The nutrients grouped for display, blanks and all.

        Groups with nothing recorded are returned too, empty — the page can
        then say "no vitamins recorded" rather than silently omitting the
        heading and leaving the reader to wonder whether it looked.
        """
        scaled = self.nutrients_for(grams)
        groups = []
        for label, fields in self.NUTRIENT_GROUPS:
            rows = [
                {
                    "field": field,
                    "label": self._meta.get_field(field).verbose_name,
                    "value": scaled[field],
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


class FoodSource(models.Model):
    """Append-only fetch log: who pulled this, when, from where, and what came back.

    Not unique on ``(api, external_id)`` on purpose — this is history. Every
    refetch appends a row, so a normaliser bug can be fixed against the
    original payload without going back to the network.
    """

    entry = models.ForeignKey(FoodEntry, on_delete=models.CASCADE, related_name="sources")
    api = models.CharField(max_length=16, choices=SourceAPI.choices)
    external_id = models.CharField(max_length=128, blank=True)
    fetched_at = models.DateTimeField(default=timezone.now)
    fetched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-fetched_at"]

    def __str__(self):
        return f"{self.entry_id} from {self.api} at {self.fetched_at:%Y-%m-%d}"


class FoodPortion(models.Model):
    """What one of something weighs, for this food.

    The answer to "2 cloves of garlic". Without a row here, a count unit
    cannot be converted and the converter says so rather than guessing.
    """

    entry = models.ForeignKey(FoodEntry, on_delete=models.CASCADE, related_name="portions")
    unit = models.CharField(
        max_length=8,
        choices=Unit.choices,
        blank=True,
        help_text="Leave empty for the weight of one unqualified item — '2 eggs'.",
    )
    grams = models.DecimalField(
        max_digits=8, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    note = models.CharField(max_length=80, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["entry", "unit"], name="unique_portion_per_unit")
        ]
        ordering = ["unit"]

    def __str__(self):
        return f"1 {self.get_unit_display() or 'item'} = {self.grams} g"


class IngredientDefault(models.Model):
    """Per user: when a recipe line says this ingredient, use this food.

    Optional. Without one the picker opens; with one, nutrition and stock
    resolve silently. Per user because two people can reasonably disagree
    about which brand of stock cube "stock cube" means.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="food_defaults"
    )
    ingredient = models.ForeignKey(
        Ingredient, on_delete=models.CASCADE, related_name="food_defaults"
    )
    entry = models.ForeignKey(FoodEntry, on_delete=models.CASCADE, related_name="defaulted_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "ingredient"], name="unique_default_per_term")
        ]
        ordering = ["ingredient__name"]

    def __str__(self):
        return f"{self.ingredient} → {self.entry}"


class UserAPICredential(models.Model):
    """A user's own FoodData Central key, encrypted at rest.

    Open Food Facts needs none, so having no key is a normal state, not an
    error: the FDC leg of a lookup reports ``missing_key`` and the rest of
    the lookup still returns results.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_credentials"
    )
    api = models.CharField(max_length=16, choices=SourceAPI.choices)
    key = EncryptedTextField()
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "api"], name="unique_credential_per_api")
        ]

    def __str__(self):
        return f"{self.get_api_display()} key for {self.user}"


# --- Stock ------------------------------------------------------------------


class PantryItem(models.Model):
    """One lot of one food in one person's kitchen.

    A lot, not a running total: two boxes bought a month apart expire on
    different days, and "what goes off first" is a question people actually
    ask. ``quantity_g`` is the current balance, kept in step with the ledger
    by ``services.stock``.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pantry_items"
    )
    entry = models.ForeignKey(FoodEntry, on_delete=models.PROTECT, related_name="stocked_as")
    quantity_g = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    acquired_on = models.DateField(default=timezone.localdate)
    expires_on = models.DateField(null=True, blank=True)
    location = models.CharField(
        max_length=40, blank=True, help_text="Cupboard, fridge, freezer, wherever."
    )
    note = models.CharField(max_length=140, blank=True)

    class Meta:
        ordering = ["expires_on", "acquired_on"]
        indexes = [models.Index(fields=["owner", "entry"])]

    def __str__(self):
        return f"{self.quantity_g} g {self.entry}"

    @property
    def is_empty(self):
        return self.quantity_g <= 0

    @property
    def days_left(self):
        if not self.expires_on:
            return None
        return (self.expires_on - timezone.localdate()).days


class MovementKind(models.TextChoices):
    """Why stock moved.

    ``CONSUME`` and ``EATEN`` are the distinction that matters. Cooking a
    batch draws stock but feeds nobody — the food still exists, as a meal in
    the fridge. Only ``EATEN`` counts as intake, and it follows the eater
    rather than whoever owned the lot.
    """

    ADD = "add", "Bought or restocked"
    CONSUME = "consume", "Used in cooking"
    EATEN = "eaten", "Eaten"
    DISCARD = "discard", "Thrown out"
    ADJUST = "adjust", "Corrected by hand"


class StockMovement(models.Model):
    """Append-only ledger. Nothing here is ever edited, only reversed."""

    item = models.ForeignKey(PantryItem, on_delete=models.CASCADE, related_name="movements")
    kind = models.CharField(max_length=10, choices=MovementKind.choices)
    grams = models.DecimalField(
        max_digits=10, decimal_places=2, help_text="Positive adds, negative removes."
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    recipe = models.ForeignKey(
        "recipes.Recipe", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="stock_movements",
    )
    happened_at = models.DateTimeField(default=timezone.now)
    note = models.CharField(max_length=140, blank=True)

    class Meta:
        ordering = ["-happened_at", "-pk"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.grams} g"

    @classmethod
    def balance_for(cls, item):
        """Recompute a lot's balance from its ledger. The audit, not the norm."""
        total = cls.objects.filter(item=item).aggregate(total=Sum("grams"))["total"]
        return total or Decimal("0")
