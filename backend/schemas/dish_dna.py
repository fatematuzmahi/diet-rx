"""SCRUM-47: Pydantic schemas for Dish DNA (nutritional fingerprint of a dish).

A "Dish DNA" record is the nutritional fingerprint of a single dish, stored per
serving:

* Macro breakdown -- calories, protein_g, carbs_g, fat_g (plus fiber_g, sugar_g)
* Metabolic markers -- glycemic_index, sodium_mg, saturated_fat_g
* Vitamins -- vitamin_a_mcg, vitamin_c_mg, vitamin_d_mcg, vitamin_k_mcg, b_vitamin_mg
* Minerals -- calcium_mg, iron_mg, potassium_mg
* Diet metadata -- diet_tags (comma separated), allergens (comma separated)

Units live in the field names so no scale has to be guessed anywhere.

Derived health statuses
-----------------------
The UI renders dynamic badges such as "High Protein", "Low Sodium" or
"Diabetic Friendly". Those badges are DERIVED here, server-side, from the
stored nutrients by :func:`derive_health_statuses`, so the rule set is
unit-testable and every client renders the same verdict for the same dish.
The thresholds are the constants below.

Decision support
----------------
Every response carries the SRS decision-support disclaimer: DietRx informs, it
does not replace professional medical advice.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

# Required by the SRS: surfaced on every Dish DNA API response so the system is
# never mistaken for a replacement for professional medical advice.
DECISION_SUPPORT_DISCLAIMER = (
    "DietRx is a decision-support system and does not replace professional "
    "medical advice."
)

# --------------------------------------------------------------------------- #
# Health-status badge catalogue
# --------------------------------------------------------------------------- #
STATUS_HIGH_PROTEIN = "HIGH_PROTEIN"
STATUS_LOW_SODIUM = "LOW_SODIUM"
STATUS_DIABETIC_FRIENDLY = "DIABETIC_FRIENDLY"
STATUS_LOW_FAT = "LOW_FAT"
STATUS_HIGH_FIBER = "HIGH_FIBER"
STATUS_LOW_CARB = "LOW_CARB"
STATUS_VITAMIN_RICH = "VITAMIN_RICH"
STATUS_CALORIE_LIGHT = "CALORIE_LIGHT"

#: Human-readable label for each badge, used by the UI and the API docs.
HEALTH_STATUS_LABELS = {
    STATUS_HIGH_PROTEIN: "High Protein",
    STATUS_LOW_SODIUM: "Low Sodium",
    STATUS_DIABETIC_FRIENDLY: "Diabetic Friendly",
    STATUS_LOW_FAT: "Low Fat",
    STATUS_HIGH_FIBER: "High Fiber",
    STATUS_LOW_CARB: "Low Carb",
    STATUS_VITAMIN_RICH: "Vitamin Rich",
    STATUS_CALORIE_LIGHT: "Calorie Light",
}

# --------------------------------------------------------------------------- #
# Thresholds that drive the badges (all per serving)
# --------------------------------------------------------------------------- #
HIGH_PROTEIN_G = 20.0                 # >= 20 g protein per serving
LOW_SODIUM_MG = 140.0                 # <= 140 mg sodium (FDA "low sodium")
DIABETIC_MAX_GLYCEMIC_INDEX = 55.0    # <= 55 GI
LOW_FAT_MAX_SATURATED_G = 1.5         # <= 1.5 g saturated fat
HIGH_FIBER_G = 3.0                    # >= 3 g fibre
LOW_CARB_G = 10.0                     # <= 10 g carbs
VITAMIN_RICH_C_MG = 20.0              # >= 20 mg vitamin C
VITAMIN_RICH_A_MCG = 300.0            # or >= 300 mcg vitamin A
CALORIE_LIGHT_KCAL = 150.0            # <= 150 kcal per serving


def derive_health_statuses(
    *,
    protein_g: Optional[float] = None,
    carbs_g: Optional[float] = None,
    fat_g: Optional[float] = None,
    fiber_g: Optional[float] = None,
    sodium_mg: Optional[float] = None,
    saturated_fat_g: Optional[float] = None,
    glycemic_index: Optional[float] = None,
    calories: Optional[float] = None,
    vitamin_a_mcg: Optional[float] = None,
    vitamin_c_mg: Optional[float] = None,
) -> List[str]:
    """Return the health-status badge codes implied by a dish's nutrients.

    A ``None`` nutrient simply does not contribute a badge -- it is never
    treated as zero, so a dish with an unknown sodium level is not silently
    labelled "Low Sodium". The returned list preserves the catalogue order
    above, which keeps the UI (and the tests) deterministic.
    """
    statuses: List[str] = []

    if protein_g is not None and protein_g >= HIGH_PROTEIN_G:
        statuses.append(STATUS_HIGH_PROTEIN)

    if sodium_mg is not None and sodium_mg <= LOW_SODIUM_MG:
        statuses.append(STATUS_LOW_SODIUM)

    if glycemic_index is not None and glycemic_index <= DIABETIC_MAX_GLYCEMIC_INDEX:
        statuses.append(STATUS_DIABETIC_FRIENDLY)

    if saturated_fat_g is not None and saturated_fat_g <= LOW_FAT_MAX_SATURATED_G:
        statuses.append(STATUS_LOW_FAT)

    if fiber_g is not None and fiber_g >= HIGH_FIBER_G:
        statuses.append(STATUS_HIGH_FIBER)

    if carbs_g is not None and carbs_g <= LOW_CARB_G:
        statuses.append(STATUS_LOW_CARB)

    if (
        vitamin_c_mg is not None and vitamin_c_mg >= VITAMIN_RICH_C_MG
    ) or (vitamin_a_mcg is not None and vitamin_a_mcg >= VITAMIN_RICH_A_MCG):
        statuses.append(STATUS_VITAMIN_RICH)

    if calories is not None and calories <= CALORIE_LIGHT_KCAL:
        statuses.append(STATUS_CALORIE_LIGHT)

    return statuses


def _split_tags(value: Optional[str]) -> List[str]:
    """Split a comma separated tag column into a clean list of tags."""
    if not value:
        return []
    return [tag.strip() for tag in value.split(",") if tag.strip()]


def _to_float(value: Any) -> Optional[float]:
    """Convert an Oracle NUMBER (``Decimal``/``int``) to a plain float.

    ``None`` is preserved so an unknown nutrient stays unknown instead of
    silently becoming 0 -- that distinction drives the health badges. A value
    that cannot be converted is treated as unknown as well rather than failing
    the whole response.
    """
    if value is None:
        return None
    if isinstance(value, float):
        return value
    if isinstance(value, (int, Decimal)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_text(value: Any) -> Optional[str]:
    """Read a VARCHAR2/CLOB column as text.

    Oracle returns a CLOB as a LOB object whose content is only reachable via
    ``read()`` (or ``str()``); both are tried.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    read = getattr(value, "read", None)
    if callable(read):
        try:
            return str(read())
        except Exception:  # pragma: no cover - defensive, LOB read failure
            return None
    return str(value)



# --------------------------------------------------------------------------- #
# Request body: POST /api/dish-dna
# --------------------------------------------------------------------------- #
class DishDnaCreate(BaseModel):
    """Fields accepted when a clinician adds a new Dish DNA record.

    Only the dish identity and its nutrients are writable; ``dish_id`` is a
    GENERATED ALWAYS AS IDENTITY column and is deliberately NOT a field --
    Oracle allocates it. ``created_at``/``updated_at`` are server managed.
    """

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=150)
    cuisine: Optional[str] = Field(default=None, max_length=100)
    category: Optional[str] = Field(default=None, max_length=80)
    description: Optional[str] = Field(default=None, max_length=500)
    ingredients: Optional[str] = None

    serving_size_g: Optional[float] = Field(default=None, gt=0, le=10000)
    serving_description: Optional[str] = Field(default=None, max_length=120)

    calories: Optional[float] = Field(default=None, ge=0, le=10000)
    protein_g: Optional[float] = Field(default=None, ge=0, le=1000)
    carbs_g: Optional[float] = Field(default=None, ge=0, le=1000)
    fat_g: Optional[float] = Field(default=None, ge=0, le=1000)
    fiber_g: Optional[float] = Field(default=None, ge=0, le=1000)
    sugar_g: Optional[float] = Field(default=None, ge=0, le=1000)

    glycemic_index: Optional[float] = Field(default=None, ge=0, le=150)
    sodium_mg: Optional[float] = Field(default=None, ge=0, le=100000)
    saturated_fat_g: Optional[float] = Field(default=None, ge=0, le=1000)

    vitamin_a_mcg: Optional[float] = Field(default=None, ge=0, le=100000)
    vitamin_c_mg: Optional[float] = Field(default=None, ge=0, le=100000)
    vitamin_d_mcg: Optional[float] = Field(default=None, ge=0, le=100000)
    vitamin_k_mcg: Optional[float] = Field(default=None, ge=0, le=100000)
    b_vitamin_mg: Optional[float] = Field(default=None, ge=0, le=100000)

    calcium_mg: Optional[float] = Field(default=None, ge=0, le=100000)
    iron_mg: Optional[float] = Field(default=None, ge=0, le=100000)
    potassium_mg: Optional[float] = Field(default=None, ge=0, le=100000)

    diet_tags: Optional[str] = Field(default=None, max_length=300)
    allergens: Optional[str] = Field(default=None, max_length=300)


# --------------------------------------------------------------------------- #
# Request body: PUT /api/dish-dna/{dish_id}
# --------------------------------------------------------------------------- #
class DishDnaUpdate(BaseModel):
    """Partial update of a Dish DNA record.

    Every field is optional; only the fields actually supplied are written.
    ``None`` means "not supplied" and leaves the stored column untouched --
    the endpoint uses an explicit "fields supplied" check so a missing value
    never blanks a column by accident.
    """

    model_config = ConfigDict(extra="ignore")

    name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    cuisine: Optional[str] = Field(default=None, max_length=100)
    category: Optional[str] = Field(default=None, max_length=80)
    description: Optional[str] = Field(default=None, max_length=500)
    ingredients: Optional[str] = None

    serving_size_g: Optional[float] = Field(default=None, gt=0, le=10000)
    serving_description: Optional[str] = Field(default=None, max_length=120)

    calories: Optional[float] = Field(default=None, ge=0, le=10000)
    protein_g: Optional[float] = Field(default=None, ge=0, le=1000)
    carbs_g: Optional[float] = Field(default=None, ge=0, le=1000)
    fat_g: Optional[float] = Field(default=None, ge=0, le=1000)
    fiber_g: Optional[float] = Field(default=None, ge=0, le=1000)
    sugar_g: Optional[float] = Field(default=None, ge=0, le=1000)

    glycemic_index: Optional[float] = Field(default=None, ge=0, le=150)
    sodium_mg: Optional[float] = Field(default=None, ge=0, le=100000)
    saturated_fat_g: Optional[float] = Field(default=None, ge=0, le=1000)

    vitamin_a_mcg: Optional[float] = Field(default=None, ge=0, le=100000)
    vitamin_c_mg: Optional[float] = Field(default=None, ge=0, le=100000)
    vitamin_d_mcg: Optional[float] = Field(default=None, ge=0, le=100000)
    vitamin_k_mcg: Optional[float] = Field(default=None, ge=0, le=100000)
    b_vitamin_mg: Optional[float] = Field(default=None, ge=0, le=100000)

    calcium_mg: Optional[float] = Field(default=None, ge=0, le=100000)
    iron_mg: Optional[float] = Field(default=None, ge=0, le=100000)
    potassium_mg: Optional[float] = Field(default=None, ge=0, le=100000)

    diet_tags: Optional[str] = Field(default=None, max_length=300)
    allergens: Optional[str] = Field(default=None, max_length=300)


# --------------------------------------------------------------------------- #
# Query parameters: GET /api/dish-dna
# --------------------------------------------------------------------------- #
class DishDnaFilters(BaseModel):
    """Search / filter criteria accepted by the list endpoint.

    All filters are optional and are combined with AND. They map 1:1 onto the
    router's query parameters so the UI filter bar and the API contract stay
    in step.
    """

    search: Optional[str] = Field(default=None, max_length=150)
    cuisine: Optional[str] = Field(default=None, max_length=100)
    category: Optional[str] = Field(default=None, max_length=80)
    diet_tag: Optional[str] = Field(default=None, max_length=80)
    max_glycemic_index: Optional[float] = Field(default=None, ge=0, le=150)
    max_sodium_mg: Optional[float] = Field(default=None, ge=0, le=100000)
    min_protein_g: Optional[float] = Field(default=None, ge=0, le=1000)
    max_calories: Optional[float] = Field(default=None, ge=0, le=10000)



# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #
class DishDnaResponse(BaseModel):
    """A single Dish DNA record as returned by the API.

    ``health_statuses`` holds the derived badge codes (see
    :func:`derive_health_statuses`) and ``health_status_labels`` their
    human-readable labels, so the UI does not have to duplicate the mapping.
    """

    dish_id: int
    name: str
    cuisine: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    ingredients: Optional[str] = None

    serving_size_g: Optional[float] = None
    serving_description: Optional[str] = None

    calories: Optional[float] = None
    protein_g: Optional[float] = None
    carbs_g: Optional[float] = None
    fat_g: Optional[float] = None
    fiber_g: Optional[float] = None
    sugar_g: Optional[float] = None

    glycemic_index: Optional[float] = None
    sodium_mg: Optional[float] = None
    saturated_fat_g: Optional[float] = None

    vitamin_a_mcg: Optional[float] = None
    vitamin_c_mg: Optional[float] = None
    vitamin_d_mcg: Optional[float] = None
    vitamin_k_mcg: Optional[float] = None
    b_vitamin_mg: Optional[float] = None

    calcium_mg: Optional[float] = None
    iron_mg: Optional[float] = None
    potassium_mg: Optional[float] = None

    diet_tags: List[str] = Field(default_factory=list)
    allergens: List[str] = Field(default_factory=list)

    health_statuses: List[str] = Field(default_factory=list)
    health_status_labels: List[str] = Field(default_factory=list)

    disclaimer: str = DECISION_SUPPORT_DISCLAIMER
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


    @classmethod
    def build(cls, row: tuple) -> "DishDnaResponse":
        """Assemble the response from a DISH_DNA row in the router's _COLS order.

        Oracle returns ``NUMBER`` columns as ``Decimal`` and a CLOB as a LOB
        object, so every value is normalised here. The derived health badges
        are computed here too, so the same rule set is applied on every read
        path (list, single fetch, create, update).
        """
        (
            dish_id, name, cuisine, category, description, ingredients,
            serving_size_g, serving_description,
            calories, protein_g, carbs_g, fat_g, fiber_g, sugar_g,
            glycemic_index, sodium_mg, saturated_fat_g,
            vitamin_a_mcg, vitamin_c_mg, vitamin_d_mcg, vitamin_k_mcg, b_vitamin_mg,
            calcium_mg, iron_mg, potassium_mg,
            diet_tags, allergens, created_at, updated_at,
        ) = row

        protein_g = _to_float(protein_g)
        carbs_g = _to_float(carbs_g)
        fat_g = _to_float(fat_g)
        fiber_g = _to_float(fiber_g)
        sodium_mg = _to_float(sodium_mg)
        saturated_fat_g = _to_float(saturated_fat_g)
        glycemic_index = _to_float(glycemic_index)
        calories = _to_float(calories)
        vitamin_a_mcg = _to_float(vitamin_a_mcg)
        vitamin_c_mg = _to_float(vitamin_c_mg)

        statuses = derive_health_statuses(
            protein_g=protein_g,
            carbs_g=carbs_g,
            fat_g=fat_g,
            fiber_g=fiber_g,
            sodium_mg=sodium_mg,
            saturated_fat_g=saturated_fat_g,
            glycemic_index=glycemic_index,
            calories=calories,
            vitamin_a_mcg=vitamin_a_mcg,
            vitamin_c_mg=vitamin_c_mg,
        )

        return cls(
            dish_id=int(dish_id),
            name=_to_text(name) or "",
            cuisine=_to_text(cuisine),
            category=_to_text(category),
            description=_to_text(description),
            ingredients=_to_text(ingredients),
            serving_size_g=_to_float(serving_size_g),
            serving_description=_to_text(serving_description),
            calories=calories,
            protein_g=protein_g,
            carbs_g=carbs_g,
            fat_g=fat_g,
            fiber_g=fiber_g,
            sugar_g=_to_float(sugar_g),
            glycemic_index=glycemic_index,
            sodium_mg=sodium_mg,
            saturated_fat_g=saturated_fat_g,
            vitamin_a_mcg=vitamin_a_mcg,
            vitamin_c_mg=vitamin_c_mg,
            vitamin_d_mcg=_to_float(vitamin_d_mcg),
            vitamin_k_mcg=_to_float(vitamin_k_mcg),
            b_vitamin_mg=_to_float(b_vitamin_mg),
            calcium_mg=_to_float(calcium_mg),
            iron_mg=_to_float(iron_mg),
            potassium_mg=_to_float(potassium_mg),
            diet_tags=_split_tags(_to_text(diet_tags)),
            allergens=_split_tags(_to_text(allergens)),
            health_statuses=statuses,
            health_status_labels=[
                HEALTH_STATUS_LABELS.get(code, code.replace("_", " ").title())
                for code in statuses
            ],
            created_at=created_at,
            updated_at=updated_at,
        )



class DishDnaListResponse(BaseModel):
    """Envelope returned by GET /api/dish-dna.

    ``total`` is the number of records matching the filters (not the number
    returned), so the UI can render "showing N of M" without a second call.
    """

    dishes: List[DishDnaResponse] = Field(default_factory=list)
    total: int = 0
    count: int = 0
    filters: DishDnaFilters = Field(default_factory=DishDnaFilters)
    disclaimer: str = DECISION_SUPPORT_DISCLAIMER

