"""USDA FoodData Central.

Authoritative for whole and generic foods, weaker on branded and
international items — the mirror image of Open Food Facts, which is why
Crumbs queries both rather than falling through from one to the other.

Needs a free API key, supplied by the user and held server-side. There is no
barcode endpoint: you search with the barcode as the query, restrict to
branded foods, and filter to exact ``gtinUpc`` equality, because the search is
fuzzy and will happily return neighbours.
"""

from __future__ import annotations

from .base import DEFAULT_TIMEOUT, NormalizedFood, SourceError, SourceModule, register, to_decimal

BASE = "https://api.nal.usda.gov/fdc/v1"
SEARCH_URL = BASE + "/foods/search"
DETAIL_URL = BASE + "/food/{fdc_id}"

#: FDC identifies nutrients by number, and the number is the whole contract:
#: a wrong one files magnesium as zinc, silently and plausibly. The first
#: eight were verified against live responses; the rest are the standard FDC
#: ids and have NOT been checked against the API from here. Spot-check one
#: import against the FDC website before trusting a vitamin figure, and see
#: `nutrient_report()` below for the quick way to do that.
#:
#: Anything not in this map is simply not imported, which is the safe
#: failure: the food arrives with that nutrient blank, and blank means
#: "nobody has said" rather than zero.
NUTRIENT_IDS = {
    # Verified
    1008: "energy_kcal",
    1003: "protein_g",
    1005: "carbohydrate_g",
    1004: "fat_g",
    1258: "saturated_fat_g",
    2000: "sugars_g",
    1079: "fibre_g",
    1093: "sodium_mg",
    # Standard ids, unverified from here
    1257: "trans_fat_g",
    1253: "cholesterol_mg",
    1092: "potassium_mg",
    1087: "calcium_mg",
    1089: "iron_mg",
    1090: "magnesium_mg",
    1095: "zinc_mg",
    1091: "phosphorus_mg",
    1106: "vitamin_a_ug",      # vitamin A, RAE
    1162: "vitamin_c_mg",      # total ascorbic acid
    1114: "vitamin_d_ug",      # D2 + D3
    1109: "vitamin_e_mg",      # alpha-tocopherol
    1185: "vitamin_k_ug",      # phylloquinone
    1165: "thiamin_mg",
    1166: "riboflavin_mg",
    1167: "niacin_mg",
    1175: "vitamin_b6_mg",
    1177: "folate_ug",         # folate, total
    1178: "vitamin_b12_ug",
}


def nutrient_report(payload):
    """What a live FDC payload offers, and what we did with it.

    A debugging aid for the mapping above: pass a raw food payload and it
    lists every nutrient FDC returned, the id, the unit, and the field it
    landed in — or that it was ignored. Checking a mapping this way takes a
    minute; noticing that iron has been arriving as calcium takes months.
    """
    rows = []
    for item in payload.get("foodNutrients") or []:
        nutrient = item.get("nutrient") or {}
        number = nutrient.get("id") or item.get("nutrientId")
        rows.append(
            {
                "id": number,
                "name": nutrient.get("name") or item.get("nutrientName") or "",
                "unit": nutrient.get("unitName") or item.get("unitName") or "",
                "value": item.get("amount", item.get("value")),
                "stored_as": NUTRIENT_IDS.get(number, "(ignored)"),
            }
        )
    return sorted(rows, key=lambda row: str(row["stored_as"]))


@register
class FoodDataCentralModule(SourceModule):
    api_code = "FDC"
    label = "USDA FoodData Central"
    requires_key = True
    supports_barcode = True

    def _get(self, url, params, api_key):
        import requests

        if not api_key:
            raise SourceError(self.api_code, "missing_key")
        try:
            response = requests.get(
                url, params={**params, "api_key": api_key}, timeout=DEFAULT_TIMEOUT
            )
        except Exception as exc:
            raise SourceError(self.api_code, f"request failed: {exc}") from exc
        if response.status_code in (401, 403):
            raise SourceError(self.api_code, "the API key was rejected")
        if response.status_code == 429:
            raise SourceError(self.api_code, "rate limit reached — try again shortly")
        if response.status_code >= 400:
            raise SourceError(self.api_code, f"HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise SourceError(self.api_code, "response was not JSON") from exc

    # -- lookups ---------------------------------------------------------

    def search_by_barcode(self, barcode, *, api_key=None):
        barcode = str(barcode).strip()
        data = self._get(
            SEARCH_URL, {"query": barcode, "dataType": "Branded", "pageSize": 25}, api_key
        )
        # The search is fuzzy; only an exact GTIN match is really this product.
        exact = [
            food
            for food in (data.get("foods") or [])
            if str(food.get("gtinUpc") or "").lstrip("0") == barcode.lstrip("0")
        ]
        return [self.normalize(food) for food in exact]

    def search_by_name(self, query, *, api_key=None, page_size=25):
        data = self._get(SEARCH_URL, {"query": query, "pageSize": min(page_size, 50)}, api_key)
        return [self.normalize(food) for food in (data.get("foods") or [])]

    def fetch_one(self, external_id, *, api_key=None):
        data = self._get(DETAIL_URL.format(fdc_id=external_id), {}, api_key)
        return self.normalize(data) if data else None

    # -- normalising -----------------------------------------------------

    def normalize(self, payload):
        """Nutrients arrive as a list of objects, and under two shapes.

        Search results use ``foodNutrients[].nutrientId`` with ``value``;
        detail responses nest it as ``nutrient.id`` with ``amount``. Both
        are already per 100 g for the data types Crumbs imports.
        """
        values = {}
        for entry in payload.get("foodNutrients") or []:
            nutrient_id = entry.get("nutrientId") or (entry.get("nutrient") or {}).get("id")
            field = NUTRIENT_IDS.get(nutrient_id)
            if not field:
                continue
            amount = entry.get("value")
            if amount is None:
                amount = entry.get("amount")
            found = to_decimal(amount)
            if found is not None:
                values[field] = found

        return NormalizedFood(
            source_api=self.api_code,
            external_id=str(payload.get("fdcId") or ""),
            name=(payload.get("description") or "Unnamed food").strip()[:255],
            brand=(payload.get("brandOwner") or payload.get("brandName") or "").strip()[:255],
            barcode=str(payload.get("gtinUpc") or "")[:64],
            serving_size=to_decimal(payload.get("servingSize")),
            serving_unit=(payload.get("servingSizeUnit") or "")[:32],
            raw=payload,
            **values,
        )
