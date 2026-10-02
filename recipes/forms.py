"""Forms for writing recipes down.

Two small liberties are taken with the plain ModelForm approach, both so that
adding a recipe feels like typing rather than like filling in a database:

* tags are a comma-separated text field, not a multi-select box;
* an ingredient is typed by name and looked up (or created) on save, so you
  never have to add "shallot" to a master list before you can use it.
"""

from django import forms
from django.forms import inlineformset_factory

from .models import (
    Ingredient,
    Note,
    Recipe,
    RecipeIngredient,
    SiteSettings,
    Step,
    Tag,
    Theme,
)


class RecipeForm(forms.ModelForm):
    tags_text = forms.CharField(
        label="Tags",
        required=False,
        help_text="Separated by commas.",
        widget=forms.TextInput(attrs={"placeholder": "weeknight, vegetarian, freezes well"}),
    )

    class Meta:
        model = Recipe
        fields = [
            "title",
            "summary",
            "image",
            "servings",
            "serving_noun",
            "prep_minutes",
            "cook_minutes",
            "difficulty",
            "tags_text",
            "notes",
            "source_name",
            "source_url",
            "is_shared",
        ]
        widgets = {
            "summary": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 4}),
            "title": forms.TextInput(attrs={"placeholder": "Sunday roast chicken", "autofocus": True}),
            "source_name": forms.TextInput(attrs={"placeholder": "Mum, or a book, or a website"}),
            "prep_minutes": forms.NumberInput(attrs={"min": 0, "placeholder": "20"}),
            "cook_minutes": forms.NumberInput(attrs={"min": 0, "placeholder": "45"}),
            "servings": forms.NumberInput(attrs={"min": 1, "max": 200}),
        }
        labels = {
            "is_shared": "Share this recipe with everyone who can reach this site",
            "serving_noun": "Measured in",
            "source_name": "Where it came from",
            "source_url": "Link",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["tags_text"].initial = ", ".join(
                self.instance.tags.values_list("name", flat=True)
            )

    def clean_tags_text(self):
        raw = self.cleaned_data.get("tags_text", "")
        names = []
        for chunk in raw.split(","):
            name = " ".join(chunk.split()).lower()
            if name and name not in names:
                names.append(name)
        if len(names) > 12:
            raise forms.ValidationError("Twelve tags is plenty — try trimming the list.")
        return names

    def save(self, commit=True):
        recipe = super().save(commit=commit)
        if commit:
            self.save_tags(recipe)
        return recipe

    def save_tags(self, recipe):
        recipe.tags.set([Tag.from_name(name) for name in self.cleaned_data["tags_text"]])


class RecipeIngredientForm(forms.ModelForm):
    name = forms.CharField(
        label="Ingredient",
        max_length=80,
        widget=forms.TextInput(attrs={"placeholder": "plain flour", "list": "known-ingredients"}),
    )

    class Meta:
        model = RecipeIngredient
        fields = ["quantity", "unit", "preparation", "group", "is_optional"]
        widgets = {
            "quantity": forms.NumberInput(attrs={"step": "any", "min": 0, "placeholder": "250"}),
            "preparation": forms.TextInput(attrs={"placeholder": "sifted"}),
            "group": forms.TextInput(attrs={"placeholder": "For the sauce"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.ingredient_id:
            self.fields["name"].initial = self.instance.ingredient.name

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())

    def save(self, commit=True):
        item = super().save(commit=False)
        item.ingredient = Ingredient.from_name(self.cleaned_data["name"])
        if commit:
            item.save()
        return item


class StepForm(forms.ModelForm):
    class Meta:
        model = Step
        fields = ["text", "minutes"]
        widgets = {
            "text": forms.Textarea(
                attrs={"rows": 2, "placeholder": "Warm the oil, then soften the onions."}
            ),
            "minutes": forms.NumberInput(attrs={"min": 0, "placeholder": "min"}),
        }
        labels = {"text": "Step", "minutes": "Timer"}


IngredientFormSet = inlineformset_factory(
    Recipe,
    RecipeIngredient,
    form=RecipeIngredientForm,
    extra=3,
    can_delete=True,
    can_order=False,
)

StepFormSet = inlineformset_factory(
    Recipe,
    Step,
    form=StepForm,
    extra=3,
    can_delete=True,
    can_order=False,
)


def apply_order(formset):
    """Renumber the surviving rows to match the order they were shown in.

    Call this after ``formset.save()``. Position is not a form field — the
    order of the rows on the page is the order, which means inserting a step
    in the middle or deleting one just works, with no numbering for the
    person to keep straight.
    """
    rows = [
        form.instance
        for form in formset.forms
        if form.cleaned_data
        and not form.cleaned_data.get("DELETE")
        and form.instance.pk is not None
    ]
    changed = []
    for position, row in enumerate(rows):
        if row.position != position:
            row.position = position
            changed.append(row)
    if changed:
        type(changed[0]).objects.bulk_update(changed, ["position"])


class SiteSettingsForm(forms.ModelForm):
    """The site half of the settings menu."""

    class Meta:
        model = SiteSettings
        fields = [
            "site_name",
            "tagline",
            "recipes_per_page",
            "default_servings",
            "share_new_recipes",
            "allow_anonymous_browsing",
        ]
        widgets = {
            "site_name": forms.TextInput(attrs={"placeholder": "Crumbs"}),
            "tagline": forms.TextInput(attrs={"placeholder": "Recipe management and viewing."}),
            "recipes_per_page": forms.NumberInput(attrs={"min": 5, "max": 100}),
            "default_servings": forms.NumberInput(attrs={"min": 1, "max": 100}),
        }
        labels = {
            "site_name": "What this place is called",
            "share_new_recipes": "Share new recipes by default",
            "allow_anonymous_browsing": "Let signed-out visitors read shared recipes",
        }

    # The theme is chosen from the gallery below the form, not from a dropdown
    # inside it, so it is deliberately absent from `fields`.


class ThemeForm(forms.ModelForm):
    """Create or edit a theme profile.

    The two colours that matter use a native colour input; the six optional
    overrides are plain text, because a colour input cannot be empty and empty
    is what "work this one out for me" looks like.
    """

    class Meta:
        model = Theme
        fields = [
            "name",
            "key",
            "description",
            "roundness",
            "primary",
            "secondary",
            *Theme.OVERRIDES,
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Midnight", "autofocus": True}),
            "key": forms.TextInput(attrs={"placeholder": "midnight"}),
            "description": forms.TextInput(
                attrs={"placeholder": "Why this theme exists, in one line."}
            ),
            "roundness": forms.TextInput(
                attrs={"placeholder": "3px", "data-theme-field": "roundness"}
            ),
            "primary": forms.TextInput(
                attrs={"type": "color", "data-theme-field": "primary"}
            ),
            "secondary": forms.TextInput(
                attrs={"type": "color", "data-theme-field": "secondary"}
            ),
        }
        help_texts = {
            "key": "Letters, numbers and hyphens. Matching a built-in theme's key replaces it.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in Theme.OVERRIDES:
            self.fields[field].widget.attrs.setdefault("placeholder", "worked out for you")
            self.fields[field].required = False
        if self.instance.pk:
            # Changing a key would orphan the site's theme selection.
            self.fields["key"].disabled = True
            self.fields["key"].help_text = "Fixed once the theme exists."

    def clean_key(self):
        key = self.cleaned_data["key"]
        if self.instance.pk:
            return self.instance.key
        return key


class NoteForm(forms.ModelForm):
    """Writing a sticky note.

    Placement is numbers rather than drag-and-drop: a percentage across its
    anchor and an angle. Less charming than dragging, but it works on a phone,
    it works without JavaScript, and the live preview beside the form shows
    what the angle actually does.
    """

    class Meta:
        model = Note
        fields = [
            "body",
            "image",
            "anchor_step",
            "visibility",
            "size",
            "icon",
            "colour",
            "offset_x",
            "offset_y",
            "rotation",
        ]
        widgets = {
            "body": forms.Textarea(
                attrs={"rows": 4, "placeholder": "Halve the sugar. Dad's version uses buttermilk.",
                       "autofocus": True, "data-note-field": "body"}
            ),
            "offset_x": forms.NumberInput(attrs={"min": 0, "max": 100, "step": 1}),
            "offset_y": forms.NumberInput(attrs={"min": 0, "max": 100, "step": 1}),
            "rotation": forms.NumberInput(
                attrs={"min": 0, "max": 359, "step": 1, "data-note-field": "rotation"}
            ),
        }
        labels = {
            "body": "What does it say?",
            "colour": "Colour",
            "image": "A picture, if it's easier to show than to say",
            "anchor_step": "Attach to",
            "visibility": "Who can read it",
            "offset_x": "Across (%)",
            "offset_y": "Down (%)",
            "rotation": "Angle (°)",
            "icon": "Shape",
        }
        help_texts = {
            "rotation": "Any angle, upside down included. Only icon themes use it.",
        }

    def __init__(self, *args, recipe=None, author=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.recipe = recipe or getattr(self.instance, "recipe", None)
        self.author = author

        # Only this recipe's steps, and only if it has any.
        steps = Step.objects.filter(recipe=self.recipe) if self.recipe else Step.objects.none()
        self.fields["anchor_step"].queryset = steps
        self.fields["anchor_step"].empty_label = "The recipe as a whole"
        if not steps.exists():
            self.fields["anchor_step"].widget = forms.HiddenInput()

        # Only the recipe's owner can post a note as part of the recipe;
        # everyone else gets private or shared.
        if not (self.author and self.recipe and self.recipe.author_id == self.author.id):
            self.fields["visibility"].choices = [
                choice
                for choice in Note.Visibility.choices
                if choice[0] != Note.Visibility.RECIPE
            ]

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("body", "").strip() and not cleaned.get("image"):
            raise forms.ValidationError("A note needs some text, a picture, or both.")
        return cleaned


class NoteShareForm(forms.Form):
    """Open one private note up to one more person."""

    username = forms.CharField(
        label="Share with",
        widget=forms.TextInput(attrs={"placeholder": "their username"}),
    )

    def __init__(self, *args, note=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.note = note

    def clean_username(self):
        from django.contrib.auth import get_user_model

        name = self.cleaned_data["username"].strip()
        user = get_user_model().objects.filter(username__iexact=name).first()
        if user is None:
            raise forms.ValidationError("No account with that username.")
        if self.note and user.id == self.note.author_id:
            raise forms.ValidationError("That's you — you can already read it.")
        return user


class AppearanceForm(forms.ModelForm):
    """The site's starting theme, for a browser that has never chosen one."""

    class Meta:
        model = SiteSettings
        fields = ["default_appearance"]
        widgets = {"default_appearance": forms.HiddenInput()}

    def clean_default_appearance(self):
        from .appearance import clean_theme

        value = clean_theme(self.cleaned_data["default_appearance"])
        if value is None:
            raise forms.ValidationError("Unknown theme.")
        return value
