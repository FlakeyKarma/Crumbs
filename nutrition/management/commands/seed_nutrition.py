"""Units, core macros and micro categories — the fixed vocabulary.

Safe to re-run: everything is matched on its slug or symbol and updated
rather than duplicated.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from nutrition.models import CoreMacro, Dimension, MicroCategory, Unit

#: symbol, name, dimension, worth in base units
UNITS = [
    ("g", "gram", Dimension.MASS, "1"),
    ("mg", "milligram", Dimension.MASS, "0.001"),
    ("\u00b5g", "microgram", Dimension.MASS, "0.000001"),
    ("kg", "kilogram", Dimension.MASS, "1000"),
    ("oz", "ounce", Dimension.MASS, "28.349523125"),
    ("lb", "pound", Dimension.MASS, "453.59237"),
    ("ml", "millilitre", Dimension.VOLUME, "1"),
    ("l", "litre", Dimension.VOLUME, "1000"),
    ("tsp", "teaspoon", Dimension.VOLUME, "4.92892159375"),
    ("tbsp", "tablespoon", Dimension.VOLUME, "14.78676478125"),
    ("cup", "cup", Dimension.VOLUME, "236.5882365"),
    ("floz", "fluid ounce", Dimension.VOLUME, "29.5735295625"),
    # Counting units. One of them weighs whatever a FoodPortion row says it
    # weighs, and nothing until then — see nutrition/services.py.
    ("clove", "clove", Dimension.COUNT, "1"),
    ("sprig", "sprig", Dimension.COUNT, "1"),
    ("slice", "slice", Dimension.COUNT, "1"),
    ("can", "can", Dimension.COUNT, "1"),
    ("pkg", "package", Dimension.COUNT, "1"),
    ("pinch", "pinch", Dimension.COUNT, "1"),
    ("kcal", "kilocalorie", Dimension.ENERGY, "1"),
    # 1 kJ = 0.239005736 kcal. Open Food Facts often gives only kilojoules.
    ("kJ", "kilojoule", Dimension.ENERGY, "0.239005736"),
    ("ea", "each", Dimension.COUNT, "1"),
]

CORE_MACROS = [
    ("energy", "Energy", 10),
    ("protein", "Protein", 20),
    ("carbohydrate", "Carbohydrate", 30),
    ("fat", "Fat", 40),
    ("fibre", "Fibre", 50),
    ("other", "Other", 90),
]

MICRO_CATEGORIES = [
    ("minerals", "Minerals", 10),
    ("vitamins", "Vitamins", 20),
    ("other", "Other", 90),
]


class Command(BaseCommand):
    help = "Load the units, core macros and micro categories."

    @transaction.atomic
    def handle(self, *args, **options):
        for symbol, name, dimension, to_base in UNITS:
            Unit.objects.update_or_create(
                symbol=symbol,
                defaults={
                    "name": name,
                    "dimension": dimension,
                    "to_base": Decimal(to_base),
                },
            )

        for slug, name, position in CORE_MACROS:
            CoreMacro.objects.update_or_create(
                slug=slug, defaults={"name": name, "position": position}
            )

        for slug, name, position in MICRO_CATEGORIES:
            MicroCategory.objects.update_or_create(
                slug=slug, defaults={"name": name, "position": position}
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"{Unit.objects.count()} units, "
                f"{CoreMacro.objects.count()} core macros, "
                f"{MicroCategory.objects.count()} micro categories."
            )
        )
