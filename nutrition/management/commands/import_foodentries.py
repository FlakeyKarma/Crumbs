"""Move the old wide FoodEntry rows onto the normalised tables.

One column becomes one row. A column that was NULL becomes no row at all,
which is the whole reason the old shape needed replacing: "nobody recorded
the iron" and "there is no iron in it" were the same empty cell, and here
the first is simply an absent row.

Re-runnable. A food already imported from the same source is updated rather
than duplicated, and its rows are rebuilt from scratch so a corrected figure
upstream lands cleanly.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from nutrition.models import CoreMacro, Food, Macro, Micro, MicroCategory, Unit

#: old field -> (core macro slug, component name, unit symbol)
MACRO_MAP = {
    "energy_kcal": ("energy", "Energy", "kcal"),
    "protein_g": ("protein", "Protein", "g"),
    "carbohydrate_g": ("carbohydrate", "Total carbohydrate", "g"),
    "sugars_g": ("carbohydrate", "Sugars", "g"),
    "fibre_g": ("fibre", "Fibre", "g"),
    "fat_g": ("fat", "Total fat", "g"),
    "saturated_fat_g": ("fat", "Saturated fat", "g"),
    "trans_fat_g": ("fat", "Trans fat", "g"),
    "cholesterol_mg": ("other", "Cholesterol", "mg"),
}

#: old field -> (category slug, name, unit symbol)
MICRO_MAP = {
    "sodium_mg": ("minerals", "Sodium", "mg"),
    "potassium_mg": ("minerals", "Potassium", "mg"),
    "calcium_mg": ("minerals", "Calcium", "mg"),
    "iron_mg": ("minerals", "Iron", "mg"),
    "magnesium_mg": ("minerals", "Magnesium", "mg"),
    "zinc_mg": ("minerals", "Zinc", "mg"),
    "phosphorus_mg": ("minerals", "Phosphorus", "mg"),
    "vitamin_a_ug": ("vitamins", "Vitamin A", "\u00b5g"),
    "vitamin_c_mg": ("vitamins", "Vitamin C", "mg"),
    "vitamin_d_ug": ("vitamins", "Vitamin D", "\u00b5g"),
    "vitamin_e_mg": ("vitamins", "Vitamin E", "mg"),
    "vitamin_k_ug": ("vitamins", "Vitamin K", "\u00b5g"),
    "thiamin_mg": ("vitamins", "Thiamin (B1)", "mg"),
    "riboflavin_mg": ("vitamins", "Riboflavin (B2)", "mg"),
    "niacin_mg": ("vitamins", "Niacin (B3)", "mg"),
    "vitamin_b6_mg": ("vitamins", "Vitamin B6", "mg"),
    "folate_ug": ("vitamins", "Folate", "\u00b5g"),
    "vitamin_b12_ug": ("vitamins", "Vitamin B12", "\u00b5g"),
}


class Command(BaseCommand):
    help = "Copy pantry.FoodEntry rows into the normalised nutrition tables."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        from pantry.models import FoodEntry

        units = {unit.symbol: unit for unit in Unit.objects.all()}
        cores = {core.slug: core for core in CoreMacro.objects.all()}
        categories = {cat.slug: cat for cat in MicroCategory.objects.all()}

        if not units or not cores:
            self.stderr.write("Run `manage.py seed_nutrition` first.")
            return

        gram = units["g"]
        source_rows = FoodEntry.all_objects.all()
        foods = macros = micros = 0

        for old in source_rows:
            rows = []
            for field, (slug, name, symbol) in MACRO_MAP.items():
                value = getattr(old, field, None)
                if value is not None:
                    rows.append(("macro", cores[slug], name, units[symbol], value))
            for field, (slug, name, symbol) in MICRO_MAP.items():
                value = getattr(old, field, None)
                if value is not None:
                    rows.append(("micro", categories[slug], name, units[symbol], value))

            if options["dry_run"]:
                self.stdout.write(f"{old.name}: {len(rows)} nutrient row(s)")
                foods += 1
                continue

            with transaction.atomic():
                food, _ = Food.objects.update_or_create(
                    source_api=old.source_api or "",
                    external_id=old.external_id or "",
                    name=old.name,
                    defaults={
                        "description": old.brand or "",
                        "reference_quantity": 100,
                        "reference_unit": gram,
                        "barcode": old.barcode or "",
                    },
                )
                # Rebuilt rather than merged: a nutrient removed upstream
                # should disappear here too, and matching row by row to work
                # that out costs more than writing eight rows again.
                food.macros.all().delete()
                food.micros.all().delete()

                for index, (kind, group, name, unit, value) in enumerate(rows):
                    if kind == "macro":
                        Macro.objects.create(
                            food=food, core_macro=group, component_name=name,
                            unit=unit, unit_count=value, position=index,
                        )
                        macros += 1
                    else:
                        Micro.objects.create(
                            food=food, category=group, micro_name=name,
                            unit=unit, unit_count=value, position=index,
                        )
                        micros += 1
                foods += 1

        if options["dry_run"]:
            self.stdout.write(f"\nWould import {foods} food(s). Nothing was written.")
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"{foods} food(s), {macros} macro row(s), {micros} micro row(s)."
            )
        )
