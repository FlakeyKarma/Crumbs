"""Create the metrics that ship with Crumbs.

    python manage.py seed_health_metrics

Safe to re-run: metrics are matched on slug and left alone if they exist, so
this never overwrites a unit someone has changed.

Nutrition metrics name a field on ``pantry.FoodEntry``. Adding a nutrient to
the Pantry later needs a row here and nothing else — the panel discovers it
through the metric, not through a code change.
"""

from django.core.management.base import BaseCommand

from health.models import Aggregation, Metric, MetricKind

BUILT_IN = [
    # slug, name, unit, kind, aggregation, nutrient_field, kcal/g, decimals, position
    ("energy", "Energy", "kcal", MetricKind.NUTRITION, Aggregation.SUM, "energy_kcal", None, 0, 10),
    ("protein", "Protein", "g", MetricKind.NUTRITION, Aggregation.SUM, "protein_g", "4.0", 0, 20),
    ("carbohydrate", "Carbohydrate", "g", MetricKind.NUTRITION, Aggregation.SUM, "carbohydrate_g", "4.0", 0, 30),
    ("fat", "Fat", "g", MetricKind.NUTRITION, Aggregation.SUM, "fat_g", "9.0", 0, 40),
    ("saturated-fat", "Saturated fat", "g", MetricKind.NUTRITION, Aggregation.SUM, "saturated_fat_g", "9.0", 0, 50),
    ("fibre", "Fibre", "g", MetricKind.NUTRITION, Aggregation.SUM, "fibre_g", None, 0, 70),
    ("trans-fat", "Trans fat", "g", MetricKind.NUTRITION, Aggregation.SUM, "trans_fat_g", "9.0", 1, 55),
    ("sugars", "Sugars", "g", MetricKind.NUTRITION, Aggregation.SUM, "sugars_g", "4.0", 0, 60),
    ("cholesterol", "Cholesterol", "mg", MetricKind.NUTRITION, Aggregation.SUM, "cholesterol_mg", None, 0, 75),

    # Minerals. Positions run 200+ so a new macro can be slotted in without
    # renumbering everything below it.
    ("sodium", "Sodium", "mg", MetricKind.NUTRITION, Aggregation.SUM, "sodium_mg", None, 0, 200),
    ("potassium", "Potassium", "mg", MetricKind.NUTRITION, Aggregation.SUM, "potassium_mg", None, 0, 210),
    ("calcium", "Calcium", "mg", MetricKind.NUTRITION, Aggregation.SUM, "calcium_mg", None, 0, 220),
    ("iron", "Iron", "mg", MetricKind.NUTRITION, Aggregation.SUM, "iron_mg", None, 1, 230),
    ("magnesium", "Magnesium", "mg", MetricKind.NUTRITION, Aggregation.SUM, "magnesium_mg", None, 0, 240),
    ("zinc", "Zinc", "mg", MetricKind.NUTRITION, Aggregation.SUM, "zinc_mg", None, 1, 250),
    ("phosphorus", "Phosphorus", "mg", MetricKind.NUTRITION, Aggregation.SUM, "phosphorus_mg", None, 0, 260),

    # Vitamins. Decimals matter here: 1.4 mg of B6 shown as "1" is wrong by
    # a third of a day's intake.
    ("vitamin-a", "Vitamin A", "\u00b5g", MetricKind.NUTRITION, Aggregation.SUM, "vitamin_a_ug", None, 0, 300),
    ("vitamin-c", "Vitamin C", "mg", MetricKind.NUTRITION, Aggregation.SUM, "vitamin_c_mg", None, 0, 310),
    ("vitamin-d", "Vitamin D", "\u00b5g", MetricKind.NUTRITION, Aggregation.SUM, "vitamin_d_ug", None, 1, 320),
    ("vitamin-e", "Vitamin E", "mg", MetricKind.NUTRITION, Aggregation.SUM, "vitamin_e_mg", None, 1, 330),
    ("vitamin-k", "Vitamin K", "\u00b5g", MetricKind.NUTRITION, Aggregation.SUM, "vitamin_k_ug", None, 0, 340),
    ("thiamin", "Thiamin (B1)", "mg", MetricKind.NUTRITION, Aggregation.SUM, "thiamin_mg", None, 2, 350),
    ("riboflavin", "Riboflavin (B2)", "mg", MetricKind.NUTRITION, Aggregation.SUM, "riboflavin_mg", None, 2, 360),
    ("niacin", "Niacin (B3)", "mg", MetricKind.NUTRITION, Aggregation.SUM, "niacin_mg", None, 1, 370),
    ("vitamin-b6", "Vitamin B6", "mg", MetricKind.NUTRITION, Aggregation.SUM, "vitamin_b6_mg", None, 2, 380),
    ("folate", "Folate", "\u00b5g", MetricKind.NUTRITION, Aggregation.SUM, "folate_ug", None, 0, 390),
    ("vitamin-b12", "Vitamin B12", "\u00b5g", MetricKind.NUTRITION, Aggregation.SUM, "vitamin_b12_ug", None, 1, 400),

    # Body. LAST, not SUM — adding up a day's weights is meaningless.
    ("weight", "Weight", "kg", MetricKind.BODY, Aggregation.LAST, "", None, 1, 100),

    ("steps", "Steps", "steps", MetricKind.MOVEMENT, Aggregation.SUM, "", None, 0, 110),
    ("active-minutes", "Active minutes", "min", MetricKind.MOVEMENT, Aggregation.SUM, "", None, 0, 120),
    ("distance", "Distance", "km", MetricKind.MOVEMENT, Aggregation.SUM, "", None, 2, 130),
]


class Command(BaseCommand):
    help = "Create the built-in health metrics."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Overwrite the built-ins back to their shipped values.",
        )

    def handle(self, *args, **options):
        created = updated = skipped = 0

        for slug, name, unit, kind, aggregation, field, per_gram, decimals, position in BUILT_IN:
            defaults = {
                "name": name,
                "unit": unit,
                "kind": kind,
                "aggregation": aggregation,
                "nutrient_field": field,
                "energy_per_gram": per_gram,
                "decimals": decimals,
                "position": position,
            }
            existing = Metric.objects.filter(slug=slug, owner__isnull=True).first()

            if existing is None:
                Metric.objects.create(slug=slug, owner=None, **defaults)
                created += 1
            elif options["reset"]:
                for key, value in defaults.items():
                    setattr(existing, key, value)
                existing.save()
                updated += 1
            else:
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"{created} created, {updated} reset, {skipped} left alone. "
                "User-defined metrics are never touched."
            )
        )
