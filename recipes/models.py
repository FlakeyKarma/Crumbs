"""Data model for Crumbs.

The shape of a recipe here is deliberately closer to a recipe card than to a
blog post: a list of ingredients that can be scaled, and an ordered list of
steps that can be worked through one at a time.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import DatabaseError, models
from django.db.models import Q
from django.urls import reverse
from django.utils.text import slugify

from .quantities import format_quantity
from .theming import validate_hex_colour, validate_roundness


def unique_slug(model, value, *, instance=None, field="slug", max_length=60):
    """Slugify ``value`` and append -2, -3 ... until it is free."""
    base = slugify(value)[:max_length] or "recipe"
    candidate = base
    counter = 2
    queryset = model._default_manager.all()
    if instance is not None and instance.pk:
        queryset = queryset.exclude(pk=instance.pk)
    while queryset.filter(**{field: candidate}).exists():
        suffix = f"-{counter}"
        candidate = f"{base[: max_length - len(suffix)]}{suffix}"
        counter += 1
    return candidate


class Tag(models.Model):
    """A loose label: 'weeknight', 'vegetarian', 'grandma'."""

    name = models.CharField(max_length=40, unique=True)
    slug = models.SlugField(max_length=50, unique=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(Tag, self.name, instance=self, max_length=50)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("recipes:tag", args=[self.slug])

    @classmethod
    def from_name(cls, name):
        """Fetch or create a tag, matching case-insensitively."""
        name = " ".join(name.split()).lower()
        existing = cls.objects.filter(name__iexact=name).first()
        return existing or cls.objects.create(name=name)


class Ingredient(models.Model):
    """A shared name, so 'smoked paprika' means the same thing everywhere."""

    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=90, unique=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = " ".join(self.name.split())
        if not self.slug:
            self.slug = unique_slug(Ingredient, self.name, instance=self, max_length=90)
        super().save(*args, **kwargs)

    @classmethod
    def from_name(cls, name):
        name = " ".join(name.split())
        existing = cls.objects.filter(name__iexact=name).first()
        return existing or cls.objects.create(name=name)


class RecipeQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Shared recipes, plus everything the viewer wrote themselves."""
        if user is not None and user.is_authenticated:
            if user.is_superuser:
                return self
            return self.filter(Q(is_shared=True) | Q(author=user))
        return self.filter(is_shared=True)

    def for_index(self):
        return self.select_related("author").prefetch_related(
            "tags", "recipe_ingredients__ingredient"
        )

    def search(self, term):
        term = term.strip()
        if not term:
            return self
        return self.filter(
            Q(title__icontains=term)
            | Q(summary__icontains=term)
            | Q(notes__icontains=term)
            | Q(tags__name__icontains=term)
            | Q(recipe_ingredients__ingredient__name__icontains=term)
        ).distinct()


class Recipe(models.Model):
    class Difficulty(models.TextChoices):
        EASY = "easy", "Easy"
        MEDIUM = "medium", "Medium"
        HARD = "hard", "Involved"

    title = models.CharField(max_length=140)
    slug = models.SlugField(max_length=160, unique=True, blank=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recipes",
    )
    summary = models.TextField(
        blank=True,
        help_text="A sentence or two. What is it, and why make it?",
    )
    image = models.ImageField(upload_to="recipes/%Y/%m/", blank=True)

    servings = models.PositiveSmallIntegerField(default=4)
    serving_noun = models.CharField(
        max_length=24,
        default="servings",
        help_text="servings, loaves, jars, dozen cookies ...",
    )
    prep_minutes = models.PositiveIntegerField(null=True, blank=True)
    cook_minutes = models.PositiveIntegerField(null=True, blank=True)
    difficulty = models.CharField(
        max_length=10, choices=Difficulty.choices, default=Difficulty.EASY
    )

    notes = models.TextField(
        blank=True, help_text="Substitutions, what went wrong last time, what to serve it with."
    )
    source_name = models.CharField(max_length=140, blank=True)
    source_url = models.URLField(blank=True)

    tags = models.ManyToManyField(Tag, blank=True, related_name="recipes")
    is_shared = models.BooleanField(
        default=False,
        help_text="Shared recipes are readable by anyone who can reach this site.",
    )

    times_cooked = models.PositiveIntegerField(default=0)
    last_cooked = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = RecipeQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["title"]),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(Recipe, self.title, instance=self, max_length=160)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("recipes:detail", args=[self.slug])

    def ingredient_trail(self, limit=6):
        """The first few ingredient names, for scanning the index.

        Reads the prefetched rows rather than querying, so this is free on a
        queryset built with ``for_index()``.
        """
        names = [item.ingredient.name for item in self.recipe_ingredients.all()]
        shown = names[:limit]
        if len(names) > limit:
            shown.append(f"and {len(names) - limit} more")
        return ", ".join(shown)

    @property
    def total_minutes(self):
        parts = [m for m in (self.prep_minutes, self.cook_minutes) if m]
        return sum(parts) if parts else None

    @property
    def has_timings(self):
        return bool(self.prep_minutes or self.cook_minutes)

    def scale_factor(self, servings):
        """How much to multiply quantities by to reach ``servings``."""
        if not servings or not self.servings:
            return Decimal("1")
        return Decimal(servings) / Decimal(self.servings)

    def ingredient_groups(self, scale=Decimal("1")):
        """Ingredients bucketed by their group heading, quantities scaled.

        Returns a list of ``(heading, rows)`` pairs. ``heading`` is an empty
        string for the main, unlabelled group, which always comes first.
        """
        buckets = {}
        order = []
        for item in self.recipe_ingredients.select_related("ingredient"):
            if item.group not in buckets:
                buckets[item.group] = []
                order.append(item.group)
            buckets[item.group].append(
                {
                    "quantity": item.formatted_quantity(scale),
                    "unit": item.get_unit_display() if item.unit else "",
                    "name": item.ingredient.name,
                    "preparation": item.preparation,
                    "is_optional": item.is_optional,
                }
            )
        # Stable sort: groups keep the order they first appear in, and the
        # unlabelled main group is hoisted to the top.
        order.sort(key=lambda heading: heading != "")
        return [(heading, buckets[heading]) for heading in order]

    def mark_cooked(self, on_date):
        self.times_cooked = models.F("times_cooked") + 1
        self.last_cooked = on_date
        self.save(update_fields=["times_cooked", "last_cooked", "updated_at"])
        self.refresh_from_db(fields=["times_cooked"])


class Unit(models.TextChoices):
    GRAM = "g", "g"
    KILOGRAM = "kg", "kg"
    OUNCE = "oz", "oz"
    POUND = "lb", "lb"
    MILLILITRE = "ml", "ml"
    LITRE = "l", "l"
    TEASPOON = "tsp", "tsp"
    TABLESPOON = "tbsp", "tbsp"
    CUP = "cup", "cup"
    FLUID_OUNCE = "floz", "fl oz"
    PINCH = "pinch", "pinch"
    CLOVE = "clove", "clove"
    SPRIG = "sprig", "sprig"
    SLICE = "slice", "slice"
    CAN = "can", "can"
    PACKAGE = "pkg", "package"


class RecipeIngredient(models.Model):
    recipe = models.ForeignKey(
        Recipe, on_delete=models.CASCADE, related_name="recipe_ingredients"
    )
    ingredient = models.ForeignKey(
        Ingredient, on_delete=models.PROTECT, related_name="used_in"
    )
    quantity = models.DecimalField(
        max_digits=9,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Leave empty for things like 'salt, to taste'.",
    )
    unit = models.CharField(max_length=8, choices=Unit.choices, blank=True)
    preparation = models.CharField(
        max_length=90, blank=True, help_text="finely chopped, at room temperature ..."
    )
    group = models.CharField(
        max_length=60,
        blank=True,
        help_text="Optional heading, e.g. 'For the sauce'.",
    )
    is_optional = models.BooleanField(default=False)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "pk"]

    def __str__(self):
        return f"{self.formatted_quantity()} {self.get_unit_display() if self.unit else ''} {self.ingredient}".strip()

    def scaled_quantity(self, scale=Decimal("1")):
        if self.quantity is None:
            return None
        return self.quantity * Decimal(scale)

    def formatted_quantity(self, scale=Decimal("1")):
        return format_quantity(self.scaled_quantity(scale))


class Step(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="steps")
    position = models.PositiveSmallIntegerField(default=0)
    text = models.TextField()
    minutes = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="If this step is mostly waiting, the timer is offered here.",
    )

    class Meta:
        ordering = ["position", "pk"]

    def __str__(self):
        return self.text[:60]


# --- Configuration held in the database -------------------------------------
#
# Two things live here rather than in settings.py: the theme profiles people
# make for themselves, and the handful of site-wide choices that shouldn't
# need a redeploy. Everything structural stays in settings.py, because a
# setting you can change from a browser is a setting an attacker can change
# from a browser.


class Theme(models.Model):
    """A theme profile created through the settings menu.

    Mirrors the shape of a ``settings.CRUMBS_THEMES`` entry. A theme stored
    here with the same key as a built-in one shadows it, which is how "copy
    and tweak a built-in" works without touching the file.
    """

    key = models.SlugField(
        max_length=40,
        unique=True,
        help_text="Short identifier, used in CRUMBS_THEME and in URLs.",
    )
    name = models.CharField(max_length=60)
    description = models.CharField(
        max_length=300,
        blank=True,
        help_text="One line, so you remember later why this theme exists.",
    )

    roundness = models.CharField(
        max_length=12,
        default="3px",
        validators=[validate_roundness],
        help_text="Corner radius on buttons, inputs and photos: 0, 6px, 0.4rem.",
    )
    primary = models.CharField(
        max_length=7,
        default="#8e2c3f",
        validators=[validate_hex_colour],
        help_text="Links, primary buttons, step numbers.",
    )
    secondary = models.CharField(
        max_length=7,
        default="#b6801c",
        validators=[validate_hex_colour],
        help_text="Running timers and warnings, and nothing else.",
    )

    # Optional overrides. Left blank, each is worked out from the two colours
    # above — see recipes/theming.py.
    primary_hover = models.CharField(
        max_length=7, blank=True, validators=[validate_hex_colour]
    )
    primary_dark = models.CharField(
        max_length=7, blank=True, validators=[validate_hex_colour]
    )
    primary_dark_hover = models.CharField(
        max_length=7, blank=True, validators=[validate_hex_colour]
    )
    secondary_dark = models.CharField(
        max_length=7, blank=True, validators=[validate_hex_colour]
    )
    on_primary = models.CharField(
        max_length=7, blank=True, validators=[validate_hex_colour]
    )
    on_primary_dark = models.CharField(
        max_length=7, blank=True, validators=[validate_hex_colour]
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    #: The optional fields, in the order the form shows them.
    OVERRIDES = (
        "primary_hover",
        "primary_dark",
        "primary_dark_hover",
        "secondary_dark",
        "on_primary",
        "on_primary_dark",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("recipes:theme-edit", args=[self.key])

    def as_profile(self):
        """The dict shape that ``theming.resolve_theme`` expects."""
        profile = {
            "key": self.key,
            "builtin": False,
            "name": self.name,
            "description": self.description,
            "roundness": self.roundness,
            "primary": self.primary,
            "secondary": self.secondary,
        }
        for field in self.OVERRIDES:
            value = getattr(self, field)
            if value:
                profile[field] = value
        return profile




class SiteSettings(models.Model):
    """The single row of site-wide preferences.

    A singleton rather than a settings module, because these are the choices a
    household actually changes: what the place is called, how many recipes fit
    on a page, whether a visitor who isn't signed in can read anything.
    """

    SINGLETON_PK = 1

    site_name = models.CharField(
        max_length=40, default="Crumbs", help_text="Shown in the corner and in page titles."
    )
    tagline = models.CharField(
        max_length=120,
        default="Recipe management and viewing.",
        blank=True,
        help_text="Shown in the footer.",
    )
    theme = models.SlugField(
        max_length=40,
        blank=True,
        default="",
        help_text="Classic colour profile. Leave empty to use CRUMBS_THEME from settings.py.",
    )
    default_appearance = models.SlugField(
        max_length=20,
        blank=True,
        default="classic",
        help_text=(
            "Which of the full themes a browser starts on before anyone "
            "picks. Readers override this for themselves."
        ),
    )

    recipes_per_page = models.PositiveSmallIntegerField(
        default=20,
        validators=[MinValueValidator(5), MaxValueValidator(100)],
    )
    default_servings = models.PositiveSmallIntegerField(
        default=4,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
        help_text="What a new recipe starts with.",
    )
    share_new_recipes = models.BooleanField(
        default=False,
        help_text="Tick the share box by default on new recipes.",
    )
    allow_anonymous_browsing = models.BooleanField(
        default=True,
        help_text=(
            "Let visitors who aren't signed in read shared recipes. Turn this "
            "off to require a sign-in for the whole site."
        ),
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "site settings"
        verbose_name_plural = "site settings"

    def __str__(self):
        return "Site settings"

    def save(self, *args, **kwargs):
        # There is only ever one row, whatever anyone does with this object.
        self.pk = self.SINGLETON_PK
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise NotImplementedError("Site settings cannot be deleted, only changed.")

    @classmethod
    def load(cls, request=None):
        """The settings row, and never a reason for a page to fail.

        Deliberately not cached between requests. A short cache would save a
        primary-key lookup, but with more than one worker process a change
        made in one of them would take the cache timeout to reach the others,
        and "I changed the theme and nothing happened" is a much worse bug
        than one indexed SELECT. Pass ``request`` to memoise it for the life
        of that request, which is where the repeat reads actually are.

        The DatabaseError branch covers the window before the first migrate,
        when a page should still render rather than 500.
        """
        if request is not None:
            cached = getattr(request, "_crumbs_site", None)
            if cached is not None:
                return cached
        try:
            row, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        except DatabaseError:
            return cls()
        if request is not None:
            request._crumbs_site = row
        return row


# --- Sticky notes -----------------------------------------------------------
#
# A note is a scrap of paper stuck to a recipe: "halve the sugar", "Dad's
# version uses buttermilk", a photo of what it should look like. How a note is
# *shown* is the theme's decision, not the note's — icon, overlay or split —
# so the note stores placement and rotation even under a theme that ignores
# them, and switching theme never loses where you put something.


class NoteQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Creator notes for everyone, your own for you, shared if you're on the list."""
        if user is not None and user.is_authenticated and user.is_superuser:
            return self
        rule = Q(visibility=Note.Visibility.RECIPE)
        if user is not None and user.is_authenticated:
            rule |= Q(author=user)
            rule |= Q(visibility=Note.Visibility.SHARED, shares__user=user)
        return self.filter(rule).distinct()

    def for_display(self):
        return self.select_related("author", "anchor_step")


class Note(models.Model):
    class Visibility(models.TextChoices):
        RECIPE = "recipe", "Part of the recipe — anyone who can see it can read this"
        PRIVATE = "private", "Just me"
        SHARED = "shared", "Me and the people I share it with"

    class Size(models.TextChoices):
        SMALL = "s", "Small"
        MEDIUM = "m", "Medium"
        LARGE = "l", "Large"

    class Icon(models.TextChoices):
        CIRCLE = "circle", "Circle"
        SQUARE = "square", "Square"
        TRIANGLE = "triangle", "Triangle"
        DIAMOND = "diamond", "Diamond"
        STAR = "star", "Star"
        PIN = "pin", "Pin"

    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notes"
    )

    body = models.TextField(blank=True)
    image = models.ImageField(upload_to="notes/%Y/%m/", blank=True)

    # Anchor is two-part on purpose. Overlay and split modes need to know
    # where in the recipe a note belongs; icon mode needs free coordinates to
    # expand from. A step (or the recipe as a whole) plus an offset covers
    # both, so one note works under every theme.
    anchor_step = models.ForeignKey(
        Step,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notes",
        help_text="Leave empty to attach the note to the recipe as a whole.",
    )
    offset_x = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("50.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Percent across its anchor. Percent, not pixels, so a note "
        "placed on a laptop doesn't land off the side of a phone.",
    )
    offset_y = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("50.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    rotation = models.SmallIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(359)],
        help_text="Any angle, upside down included. Icon themes only.",
    )

    size = models.CharField(max_length=1, choices=Size.choices, default=Size.MEDIUM)
    icon = models.CharField(max_length=10, choices=Icon.choices, default=Icon.CIRCLE)

    visibility = models.CharField(
        max_length=8, choices=Visibility.choices, default=Visibility.PRIVATE
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = NoteQuerySet.as_manager()

    class Meta:
        ordering = ["anchor_step__position", "created_at"]

    def __str__(self):
        return self.summary

    def clean(self):
        from django.core.exceptions import ValidationError

        if not self.body.strip() and not self.image:
            raise ValidationError("A note needs some text, a picture, or both.")
        if self.anchor_step_id and self.anchor_step.recipe_id != self.recipe_id:
            raise ValidationError({"anchor_step": "That step belongs to another recipe."})

    @property
    def summary(self):
        text = " ".join(self.body.split())
        if text:
            return text[:60] + ("…" if len(text) > 60 else "")
        return "Picture"

    @property
    def is_from_recipe_author(self):
        return self.visibility == self.Visibility.RECIPE

    def readable_by(self, user):
        if self.visibility == self.Visibility.RECIPE:
            return True
        if not (user and user.is_authenticated):
            return False
        if self.author_id == user.id or user.is_superuser:
            return True
        return (
            self.visibility == self.Visibility.SHARED
            and self.shares.filter(user=user).exists()
        )

    def editable_by(self, user):
        return bool(
            user and user.is_authenticated and (self.author_id == user.id or user.is_superuser)
        )


class NoteShare(models.Model):
    """Who a private note has been opened up to.

    A join table rather than a boolean, because "shared" without "with whom"
    is just public with extra steps.
    """

    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="shares")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shared_notes"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["note", "user"], name="unique_note_share")
        ]

    def __str__(self):
        return f"{self.note.summary} → {self.user}"
