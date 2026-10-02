"""Forms for the Pantry."""


from django import forms


from .models import FoodEntry, FoodPortion, PantryItem, SourceAPI


class FoodEntryForm(forms.ModelForm):
    """Entering a food by hand — the escape hatch when neither source has it.

    Twenty-seven nutrients is a lot of boxes, so the template renders them in
    the model's own groups and collapses minerals and vitamins: a label
    rarely lists them, and the common case is typing four numbers off a
    packet. Every one stays optional, and blank means *unrecorded* rather
    than zero all the way through.
    """

    class Meta:
        model = FoodEntry
        fields = [
            "name", "brand", "barcode", "image",
            *FoodEntry.NUTRIENTS,
            "serving_size", "serving_unit", "density_g_per_ml",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Rolled oats", "autofocus": True}),
            "brand": forms.TextInput(attrs={"placeholder": "optional"}),
            "barcode": forms.TextInput(attrs={"placeholder": "optional"}),
        }
        help_texts = {
            "name": "Nutrients below are all per 100 g — copy them off the label's "
                    "per-100 g column, not the per-serving one.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in FoodEntry.NUTRIENTS:
            # A label reads "0.4", not "0.400", and a spinner stepping by 1
            # is useless for micrograms.
            self.fields[field].widget.attrs.setdefault("step", "any")
            self.fields[field].widget.attrs.setdefault("min", "0")
            self.fields[field].widget.attrs.setdefault("placeholder", "—")

    def nutrient_groups(self):
        """The bound fields, grouped as the model groups them."""
        return [
            {
                "label": label,
                "fields": [self[name] for name in names],
                "filled": any(self[name].value() not in (None, "") for name in names),
            }
            for label, names in FoodEntry.NUTRIENT_GROUPS
        ]

    def save(self, commit=True):
        entry = super().save(commit=False)
        entry.source_api = SourceAPI.MANUAL
        entry.external_id = None
        if commit:
            entry.save()
        return entry


class FoodPortionForm(forms.ModelForm):
    """What one of something weighs — how count units become grams."""

    class Meta:
        model = FoodPortion
        fields = ["unit", "grams", "note"]
        widgets = {
            "grams": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "note": forms.TextInput(attrs={"placeholder": "medium, trimmed…"}),
        }


class PantryItemForm(forms.ModelForm):
    """Putting a lot of something in the cupboard."""

    class Meta:
        model = PantryItem
        fields = ["quantity_g", "acquired_on", "expires_on", "location", "note"]
        widgets = {
            "acquired_on": forms.DateInput(attrs={"type": "date"}),
            "expires_on": forms.DateInput(attrs={"type": "date"}),
            "quantity_g": forms.NumberInput(attrs={"step": "1", "min": "0", "autofocus": True}),
            "location": forms.TextInput(attrs={"placeholder": "fridge"}),
        }
        labels = {"quantity_g": "How much, in grams", "acquired_on": "Bought"}

    def clean(self):
        cleaned = super().clean()
        acquired, expires = cleaned.get("acquired_on"), cleaned.get("expires_on")
        if acquired and expires and expires < acquired:
            self.add_error("expires_on", "That's before you bought it.")
        return cleaned


class CredentialForm(forms.Form):
    """A user's own FoodData Central key."""

    key = forms.CharField(
        label="FoodData Central API key",
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
        help_text="Free from fdc.nal.usda.gov. Stored encrypted, and never sent "
                  "to the browser again once saved. Open Food Facts needs no key.",
    )


class DefaultForm(forms.Form):
    """Pinning a recipe ingredient to a specific food."""

    entry = forms.ModelChoiceField(queryset=FoodEntry.objects.none(), label="Use this food")

    def __init__(self, *args, entries=None, **kwargs):
        super().__init__(*args, **kwargs)
        if entries is not None:
            self.fields["entry"].queryset = entries


class ReferenceForm(forms.ModelForm):
    """The term itself. Its phrasings are added separately, one at a time."""

    class Meta:
        from recipes.models import Reference

        model = Reference
        fields = ["label"]
        widgets = {"label": forms.TextInput(attrs={"placeholder": "Lean beef"})}


class ReferencePatternForm(forms.ModelForm):
    """One more way of writing the term.

    Validation is the model's `clean`, which compiles the row — escaping it
    first unless the regex box is ticked. See recipes/references.py.
    """

    class Meta:
        from recipes.models import ReferencePattern

        model = ReferencePattern
        fields = ["pattern", "is_regex"]
        widgets = {
            "pattern": forms.TextInput(
                attrs={"placeholder": "beef mince", "autocomplete": "off"}
            )
        }
