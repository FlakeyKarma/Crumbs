"""Open Food Facts.

No key, ODbL, the best barcode coverage of the open sources — a barcode is one
direct call with a straight hit or miss, which makes it the right first stop
for scanning.

OFF asks for a descriptive User-Agent naming the app and a contact address
instead of a key. Set ``PANTRY_OFF_USER_AGENT``; sending the placeholder below
unchanged is poor citizenship.

Coverage is crowdsourced, so missing and wrong fields are common. That is what
``NormalizedFood.has_macros`` is for.
"""

from __future__ import annotations

import os

from .base import DEFAULT_TIMEOUT, NormalizedFood, SourceError, SourceModule, register, to_decimal

BASE = "https://world.openfoodfacts.org"
PRODUCT_URL = BASE + "/api/v2/product/{code}.json"

# Text search is NOT on /api/v2/search. That endpoint is filter-based only —
# it takes tags, categories, nutrient ranges — and it ignores an unknown
# parameter rather than rejecting it, so `search_terms` there returns a
# perfectly valid page of unrelated products. Searching for "ground beef"
# quietly matched nothing at all.
#
# Two endpoints actually do full text, so both are tried:
#   Search-a-licious, the supported replacement, POST with a JSON body;
#   /cgi/search.pl, the v1 route, still working but not recommended for new
#   integrations, kept as the fallback because it returns the same product
#   shape the barcode path already normalises.
SEARCHALICIOUS_URL = "https://search.openfoodfacts.org/search"
LEGACY_SEARCH_URL = BASE + "/cgi/search.pl"

#: Open Food Facts stores almost everything in grams, whatever the label
#: said, so a mineral in milligrams has to be scaled up on the way in. The
#: multiplier is the unit we store in divided by the gram OFF gives us.
#:
#: Minerals only. OFF's vitamin units are inconsistent between contributors
#: and products — some entries are in grams, some in the label's own unit —
#: and a silently mis-scaled vitamin is worse than a blank one, so vitamins
#: are left for the manual form or FDC until that can be checked properly.
OFF_SCALED = {
    "potassium_100g": ("potassium_mg", 1000),
    "calcium_100g": ("calcium_mg", 1000),
    "iron_100g": ("iron_mg", 1000),
    "magnesium_100g": ("magnesium_mg", 1000),
    "zinc_100g": ("zinc_mg", 1000),
    "phosphorus_100g": ("phosphorus_mg", 1000),
    "cholesterol_100g": ("cholesterol_mg", 1000),
}

#: Already in grams, stored in grams.
OFF_DIRECT = {
    "trans-fat_100g": "trans_fat_g",
}

FIELD_LIST = (
    "code",
    "product_name",
    "product_name_en",
    "generic_name",
    "brands",
    "quantity",
    "serving_size",
    "serving_quantity",
    "nutriments",
    "trans-fat_100g",
    "cholesterol_100g",
    "potassium_100g",
    "calcium_100g",
    "iron_100g",
    "magnesium_100g",
    "zinc_100g",
    "phosphorus_100g",
)
FIELDS = ",".join(FIELD_LIST)

DEFAULT_UA = "Crumbs-Pantry/0.1 (set PANTRY_OFF_USER_AGENT)"


def first_text(value, separator=","):
    """One display string out of a field whose shape varies by endpoint.

    The product and v1 search routes hand back `brands` as a comma-joined
    string; Search-a-licious indexes the same field as a list. Some fields
    also arrive language-keyed. Rather than trust whichever endpoint answered,
    take the first usable entry from any of those shapes.

    `separator=None` keeps the whole string — a product name may legitimately
    contain a comma, a brand list may not.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = next((item for item in value if item), "")
    if isinstance(value, dict):
        value = value.get("en") or next((item for item in value.values() if item), "")
    if not isinstance(value, str):
        value = str(value)
    if separator:
        value = value.split(separator)[0]
    return value.strip()


@register
class OpenFoodFactsModule(SourceModule):
    api_code = "OFF"
    label = "Open Food Facts"
    requires_key = False
    supports_barcode = True

    def _headers(self):
        return {"User-Agent": os.environ.get("PANTRY_OFF_USER_AGENT", DEFAULT_UA)}

    def _post(self, url, payload):
        import requests

        try:
            response = requests.post(
                url, json=payload, headers=self._headers(), timeout=DEFAULT_TIMEOUT
            )
        except Exception as exc:
            raise SourceError(self.api_code, f"request failed: {exc}") from exc
        if response.status_code >= 400:
            raise SourceError(self.api_code, f"HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise SourceError(self.api_code, "response was not JSON") from exc

    def _get(self, url, params=None):
        import requests  # imported here so the app loads without network deps

        try:
            response = requests.get(
                url, params=params, headers=self._headers(), timeout=DEFAULT_TIMEOUT
            )
        except Exception as exc:  # requests raises a family, and none are fatal
            raise SourceError(self.api_code, f"request failed: {exc}") from exc
        if response.status_code == 404:
            return {}
        if response.status_code >= 400:
            raise SourceError(self.api_code, f"HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise SourceError(self.api_code, "response was not JSON") from exc

    # -- lookups ---------------------------------------------------------

    def search_by_barcode(self, barcode, *, api_key=None):
        data = self._get(PRODUCT_URL.format(code=str(barcode).strip()))
        if not data or data.get("status") != 1:
            return []
        product = data.get("product") or {}
        return [self.normalize(product)] if product else []

    @staticmethod
    def _extra_nutrients(in_grams):
        """Minerals, scaled out of OFF's grams. Absent stays absent.

        Takes the full-precision reader, not the rounded one — see in_grams.
        """
        found = {}
        for key, (field, multiplier) in OFF_SCALED.items():
            grams = in_grams(key)
            if grams is not None:
                found[field] = round(grams * multiplier, 3)
        for key, field in OFF_DIRECT.items():
            grams = in_grams(key)
            if grams is not None:
                found[field] = round(grams, 3)
        return found

    def search_by_name(self, query, *, api_key=None, page_size=25):
        """Full text against Search-a-licious, falling back to the v1 route.

        The two endpoints name their result list differently — `hits` and
        `products` — so both are accepted rather than assumed. A failure in
        the first is not surfaced: the point is to return food, and the
        fallback is a working endpoint, not a consolation.
        """
        query = " ".join(str(query).split())
        if not query:
            return []

        size = max(1, min(page_size, 50))
        for fetch in (self._search_alicious, self._search_legacy):
            try:
                products = fetch(query, size)
            except SourceError:
                continue
            if products:
                return [self.normalize(p) for p in products if p]
        return []

    def _search_alicious(self, query, size):
        data = self._post(
            SEARCHALICIOUS_URL,
            {"q": query, "page_size": size, "fields": list(FIELD_LIST)},
        )
        return data.get("hits") or data.get("products") or []

    def _search_legacy(self, query, size):
        data = self._get(
            LEGACY_SEARCH_URL,
            {
                "search_terms": query,
                "search_simple": 1,
                "action": "process",
                "json": 1,
                "fields": FIELDS,
                "page_size": size,
            },
        )
        return data.get("products") or []

    def fetch_one(self, external_id, *, api_key=None):
        found = self.search_by_barcode(external_id)
        return found[0] if found else None

    # -- normalising -----------------------------------------------------

    def normalize(self, payload):
        """``nutriments`` is already per 100 g, but the keys are not reliable.

        Energy may arrive as kcal, or only as kJ; sodium is in grams where we
        store milligrams; salt sometimes stands in for sodium entirely.
        """
        nutriments = payload.get("nutriments") or {}
        if not isinstance(nutriments, dict):
            nutriments = {}

        def value(*keys, places=3):
            for key in keys:
                if key in nutriments:
                    found = to_decimal(nutriments[key], places)
                    if found is not None:
                        return found
            return None

        def in_grams(*keys):
            """Read a gram figure at full precision.

            Rounding to three places *before* multiplying up to milligrams
            throws the answer away: 0.0042 g of iron rounds to 0.004 and
            arrives as 4.0 mg instead of 4.2, and a microgram figure rounds
            to zero outright. Scale first, round after.
            """
            return value(*keys, places=9)

        energy = value("energy-kcal_100g", "energy-kcal")
        if energy is None:
            kilojoules = value("energy-kj_100g", "energy_100g")
            if kilojoules is not None:
                energy = round(kilojoules / to_decimal("4.184"), 3)

        sodium_g = in_grams("sodium_100g")
        if sodium_g is None:
            salt_g = in_grams("salt_100g")
            # Salt is sodium chloride; sodium is 39.34% of it by mass.
            sodium_g = salt_g * to_decimal("0.3934") if salt_g is not None else None

        name = (
            first_text(payload.get("product_name"), separator=None)
            or first_text(payload.get("product_name_en"), separator=None)
            or first_text(payload.get("generic_name"), separator=None)
            or "Unnamed product"
        )

        return NormalizedFood(
            source_api=self.api_code,
            external_id=str(payload.get("code") or ""),
            name=name[:255],
            brand=first_text(payload.get("brands"))[:255],
            barcode=str(payload.get("code") or "")[:64],
            energy_kcal=energy,
            protein_g=value("proteins_100g"),
            carbohydrate_g=value("carbohydrates_100g"),
            fat_g=value("fat_100g"),
            saturated_fat_g=value("saturated-fat_100g"),
            sugars_g=value("sugars_100g"),
            fibre_g=value("fiber_100g", "fibre_100g"),
            sodium_mg=round(sodium_g * 1000, 3) if sodium_g is not None else None,
            **self._extra_nutrients(in_grams),
            serving_size=to_decimal(payload.get("serving_quantity")),
            serving_unit=first_text(payload.get("serving_size"), separator=None)[:32],
            raw=payload,
        )
