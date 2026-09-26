"""Put a handful of real recipes in the box so the first page load isn't empty.

    python manage.py seed_recipes --user karma

Safe to re-run: recipes are matched on title and skipped if they already exist.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from recipes.models import Ingredient, Recipe, RecipeIngredient, Step, Tag

RECIPES = [
    {
        "title": "Sunday roast chicken",
        "summary": (
            "A whole chicken, a hot oven, and enough lemon and thyme to make the "
            "kitchen smell like a promise. The rest of the week eats off this."
        ),
        "servings": 4,
        "prep_minutes": 15,
        "cook_minutes": 75,
        "difficulty": "easy",
        "tags": ["sunday", "chicken", "leftovers"],
        "notes": (
            "Save the carcass. Cover it with water, add a halved onion and a bay "
            "leaf, and simmer for two hours while you clear up."
        ),
        "ingredients": [
            ("1.6", "kg", "whole chicken", "", "", False),
            ("60", "g", "butter", "softened", "", False),
            ("1", "", "lemon", "halved", "", False),
            ("6", "sprig", "thyme", "", "", False),
            ("4", "clove", "garlic", "unpeeled, bashed", "", False),
            ("1", "tsp", "flaky salt", "", "", False),
            ("500", "g", "new potatoes", "halved", "", True),
        ],
        "steps": [
            ("Heat the oven to 220°C fan. Take the chicken out of the fridge now — a cold bird roasts unevenly.", None),
            ("Loosen the skin over the breast with your fingers and push the softened butter underneath, as far back as you can reach.", None),
            ("Put the lemon halves, thyme and garlic inside the cavity. Salt the skin generously and set the bird breast-up in a roasting tin.", None),
            ("Roast for 20 minutes to set the skin.", 20),
            ("Drop the oven to 190°C fan, add the potatoes around the bird, and roast until the juices from the thickest part of the thigh run clear.", 55),
            ("Lift the chicken onto a board and leave it alone for 15 minutes. This is not optional; it is where the juice goes back into the meat.", 15),
        ],
    },
    {
        "title": "Brown butter banana bread",
        "summary": (
            "The browner the bananas, the better this gets. Browning the butter "
            "first is the only step that separates it from every other loaf."
        ),
        "servings": 10,
        "serving_noun": "slices",
        "prep_minutes": 20,
        "cook_minutes": 55,
        "difficulty": "easy",
        "tags": ["baking", "breakfast", "freezes well"],
        "notes": "Freezes well sliced, with baking paper between the slices.",
        "ingredients": [
            ("115", "g", "butter", "", "", False),
            ("3", "", "very ripe bananas", "mashed", "", False),
            ("150", "g", "light brown sugar", "", "", False),
            ("2", "", "eggs", "", "", False),
            ("1", "tsp", "vanilla extract", "", "", False),
            ("200", "g", "plain flour", "", "", False),
            ("1", "tsp", "bicarbonate of soda", "", "", False),
            ("0.5", "tsp", "fine salt", "", "", False),
            ("80", "g", "walnuts", "roughly chopped", "", True),
        ],
        "steps": [
            ("Melt the butter in a light-coloured pan over medium heat and keep going past melted. It will foam, then quieten, then smell like toffee and show brown flecks. Pour it into a bowl straight away.", 8),
            ("Heat the oven to 170°C fan and line a 900 g loaf tin.", None),
            ("Whisk the sugar into the warm butter, then the eggs one at a time, then the mashed banana and vanilla.", None),
            ("Fold in the flour, bicarbonate of soda and salt until you can no longer see dry flour. Stop there — this is not a batter that rewards enthusiasm.", None),
            ("Scrape into the tin, scatter the walnuts over, and bake until a skewer comes out with damp crumbs rather than batter.", 55),
            ("Cool in the tin for 10 minutes, then turn out onto a rack.", 10),
        ],
    },
    {
        "title": "Weeknight red lentil dal",
        "summary": "Store-cupboard start to finish, and better on the second day.",
        "servings": 4,
        "prep_minutes": 10,
        "cook_minutes": 30,
        "difficulty": "easy",
        "tags": ["weeknight", "vegetarian", "one pot"],
        "ingredients": [
            ("250", "g", "red lentils", "rinsed", "", False),
            ("1", "", "onion", "finely diced", "", False),
            ("3", "clove", "garlic", "sliced", "", False),
            ("20", "g", "ginger", "grated", "", False),
            ("2", "tbsp", "neutral oil", "", "", False),
            ("1", "tsp", "ground turmeric", "", "", False),
            ("1", "tsp", "ground cumin", "", "", False),
            ("400", "ml", "coconut milk", "", "", False),
            ("600", "ml", "water", "", "", False),
            ("1", "tsp", "fine salt", "", "", False),
            ("2", "tbsp", "ghee", "", "For the tempering", False),
            ("1", "tsp", "black mustard seeds", "", "For the tempering", False),
            ("2", "", "dried red chillies", "", "For the tempering", True),
        ],
        "steps": [
            ("Soften the onion in the oil over medium heat until translucent and just starting to colour.", 8),
            ("Add the garlic, ginger, turmeric and cumin and stir for a minute, until the kitchen smells of it.", 1),
            ("Tip in the lentils, coconut milk, water and salt. Bring to a simmer and cook, stirring now and then, until the lentils have collapsed.", 25),
            ("For the tempering, heat the ghee in a small pan, add the mustard seeds, and wait for them to pop. Add the chillies for a few seconds, then pour the whole lot over the dal.", 3),
        ],
    },
]


class Command(BaseCommand):
    help = "Load a few starter recipes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            default=None,
            help="Username to own the recipes. Defaults to the first superuser.",
        )
        parser.add_argument(
            "--shared",
            action="store_true",
            help="Mark the seeded recipes as shared.",
        )

    def handle(self, *args, **options):
        User = get_user_model()

        if options["user"]:
            try:
                author = User.objects.get(username=options["user"])
            except User.DoesNotExist:
                self.stderr.write(f"No user named {options['user']!r}.")
                return
        else:
            author = User.objects.filter(is_superuser=True).order_by("pk").first()
            if author is None:
                self.stderr.write(
                    "No superuser found. Run `python manage.py createsuperuser` "
                    "first, or pass --user."
                )
                return

        created = 0
        for payload in RECIPES:
            if Recipe.objects.filter(title=payload["title"]).exists():
                self.stdout.write(f"Skipping {payload['title']!r} — already here.")
                continue
            self.build(payload, author, shared=options["shared"])
            created += 1
            self.stdout.write(self.style.SUCCESS(f"Added {payload['title']!r}."))

        self.stdout.write(f"Done. {created} recipe(s) added, owned by {author.get_username()}.")

    @transaction.atomic
    def build(self, payload, author, *, shared):
        recipe = Recipe.objects.create(
            title=payload["title"],
            author=author,
            summary=payload.get("summary", ""),
            servings=payload.get("servings", 4),
            serving_noun=payload.get("serving_noun", "servings"),
            prep_minutes=payload.get("prep_minutes"),
            cook_minutes=payload.get("cook_minutes"),
            difficulty=payload.get("difficulty", "easy"),
            notes=payload.get("notes", ""),
            is_shared=shared,
        )
        recipe.tags.set([Tag.from_name(name) for name in payload.get("tags", [])])

        for position, row in enumerate(payload["ingredients"]):
            quantity, unit, name, preparation, group, optional = row
            RecipeIngredient.objects.create(
                recipe=recipe,
                ingredient=Ingredient.from_name(name),
                quantity=Decimal(quantity) if quantity else None,
                unit=unit,
                preparation=preparation,
                group=group,
                is_optional=optional,
                position=position,
            )

        for position, (text, minutes) in enumerate(payload["steps"]):
            Step.objects.create(recipe=recipe, position=position, text=text, minutes=minutes)

        return recipe
