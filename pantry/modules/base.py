"""The contract every nutrition source implements.

A source module knows how to talk to one external API and how to turn its
answer into a ``NormalizedFood``. Nothing outside this package knows that FDC
returns an array of nutrient objects keyed by numeric id while Open Food Facts
returns a flat dict of inconsistently named keys — that difference stops here.

Adding a source means writing one module and calling ``register`` on it.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal, InvalidOperation

DEFAULT_TIMEOUT = 8


class SourceError(Exception):
    """A source could not answer. Never fatal — other sources may still have."""

    def __init__(self, api_code, message):
        self.api_code = api_code
        super().__init__(message)


def to_decimal(value, places=3):
    if value is None or value == "":
        return None
    try:
        return round(Decimal(str(value)), places)
    except (InvalidOperation, TypeError, ValueError):
        return None


@dataclasses.dataclass
class NormalizedFood:
    """One food, per 100 g, however the source expressed it.

    ``raw`` keeps the untouched payload. It costs almost nothing and is the
    only way to fix a normaliser bug retrospectively without refetching
    everything.
    """

    source_api: str
    external_id: str
    name: str
    brand: str = ""
    barcode: str = ""

    # Must stay in step with FoodEntry.NUTRIENTS — `services.import_food`
    # copies every one of those names off this object, so a field here that
    # the model has and this does not is an AttributeError on first import.
    # `tests/test_pantry.py` asserts the two lists match.
    energy_kcal: Decimal | None = None
    protein_g: Decimal | None = None
    carbohydrate_g: Decimal | None = None
    fat_g: Decimal | None = None
    saturated_fat_g: Decimal | None = None
    trans_fat_g: Decimal | None = None
    sugars_g: Decimal | None = None
    fibre_g: Decimal | None = None
    cholesterol_mg: Decimal | None = None

    sodium_mg: Decimal | None = None
    potassium_mg: Decimal | None = None
    calcium_mg: Decimal | None = None
    iron_mg: Decimal | None = None
    magnesium_mg: Decimal | None = None
    zinc_mg: Decimal | None = None
    phosphorus_mg: Decimal | None = None

    vitamin_a_ug: Decimal | None = None
    vitamin_c_mg: Decimal | None = None
    vitamin_d_ug: Decimal | None = None
    vitamin_e_mg: Decimal | None = None
    vitamin_k_ug: Decimal | None = None
    thiamin_mg: Decimal | None = None
    riboflavin_mg: Decimal | None = None
    niacin_mg: Decimal | None = None
    vitamin_b6_mg: Decimal | None = None
    folate_ug: Decimal | None = None
    vitamin_b12_ug: Decimal | None = None

    serving_size: Decimal | None = None
    serving_unit: str = ""

    raw: dict = dataclasses.field(default_factory=dict)

    NUTRIENTS = (
        "energy_kcal", "protein_g", "carbohydrate_g", "fat_g",
        "saturated_fat_g", "trans_fat_g", "sugars_g", "fibre_g",
        "cholesterol_mg",
        "sodium_mg", "potassium_mg", "calcium_mg", "iron_mg",
        "magnesium_mg", "zinc_mg", "phosphorus_mg",
        "vitamin_a_ug", "vitamin_c_mg", "vitamin_d_ug", "vitamin_e_mg",
        "vitamin_k_ug", "thiamin_mg", "riboflavin_mg", "niacin_mg",
        "vitamin_b6_mg", "folate_ug", "vitamin_b12_ug",
    )

    @property
    def has_macros(self):
        """Whether this is worth showing at all.

        Crowdsourced data often has a name and nothing else. The picker uses
        this to mark an entry as unusable rather than letting someone import
        a food with no numbers in it.
        """
        return any(getattr(self, field) is not None
                   for field in ("energy_kcal", "protein_g", "carbohydrate_g", "fat_g"))

    @property
    def fingerprint(self):
        """Cheap identity for clustering near-duplicates in the picker.

        Barcode plus the four macros rounded to whole numbers. The same
        product fetched from both sources lands in one cluster, so the picker
        shows one choice rather than two checkboxes.
        """
        macros = tuple(
            int(getattr(self, field)) if getattr(self, field) is not None else None
            for field in ("energy_kcal", "protein_g", "carbohydrate_g", "fat_g")
        )
        return (self.barcode or "", macros)

    def as_dict(self):
        data = dataclasses.asdict(self)
        data.pop("raw", None)
        for key, value in data.items():
            if isinstance(value, Decimal):
                data[key] = str(value)
        return data


class SourceModule:
    """Base class. Subclasses set the flags and implement the three lookups."""

    api_code = ""
    label = ""
    requires_key = False
    supports_barcode = False

    def search_by_barcode(self, barcode, *, api_key=None):
        raise NotImplementedError

    def search_by_name(self, query, *, api_key=None, page_size=25):
        raise NotImplementedError

    def fetch_one(self, external_id, *, api_key=None):
        """Refetch a single food by its id. Import goes through this, never
        through a payload the client sent us."""
        raise NotImplementedError

    def normalize(self, payload):
        raise NotImplementedError


_REGISTRY = {}


def register(module_class):
    instance = module_class()
    _REGISTRY[instance.api_code] = instance
    return module_class


def get_module(api_code):
    return _REGISTRY.get(api_code)


def all_modules():
    return list(_REGISTRY.values())
