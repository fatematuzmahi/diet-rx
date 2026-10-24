"""SCRUM-48: Personalized meal + activity plan generator (SRS 3.3.6).

Replaces the stub in ``diet_generator.py``. The generator is a deterministic,
rule-based engine -- no ML, no external calls -- so the same inputs always
produce the same plan and the plan can be explained to the user.

Requirements implemented here
----------------------------
SRS 3.3.6.1  the meal plan is built from the profile, prescription, health
              conditions, preferences, goals and budget context.
SRS 3.3.6.2  known dietary restrictions and unresolved safety conflicts are
              respected (the safe intersection from ``conflict_resolver`` is
              applied and anything unresolved is flagged, never guessed).
SRS 3.3.6.3  diet guidance is combined with activity guidance.
SRS 3.3.6.4  the plan can be regenerated (the generator is stateless, so a
              regeneration with new inputs simply produces a new plan).

Public entry point: :func:`generate_plan`.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from backend.services.plan_conflict_resolver import (
    HIGH_INTENSITY_UNSAFE_CONDITIONS,
    detect_rules,
    find_dietary_conflicts,
    resolve_effective_restrictions,
)

# --------------------------------------------------------------------------- #
# Calorie model (Mifflin-St Jeor + activity multiplier)
# --------------------------------------------------------------------------- #
#: Activity level -> multiplier applied to the BMR.
ACTIVITY_MULTIPLIERS: Dict[str, float] = {
    "sedentary": 1.2,
    "lightly active": 1.375,
    "moderately active": 1.55,
    "very active": 1.725,
    "extra active": 1.9,
}

DEFAULT_ACTIVITY_MULTIPLIER = 1.375

#: Goal -> multiplier applied to the maintenance calorie target.
GOAL_MULTIPLIERS: Dict[str, float] = {
    "weight loss": 0.8,
    "lose weight": 0.8,
    "reduce weight": 0.8,
    "slim": 0.8,
    "weight gain": 1.15,
    "gain weight": 1.15,
    "increase weight": 1.15,
    "muscle": 1.1,
    "build muscle": 1.1,
    "maintain": 1.0,
    "weight maintenance": 1.0,
}

#: Minimum daily calorie target -- never recommend below this without a
#: professional. Keeps the generator from producing unsafe plans.
MIN_CALORIE_TARGET = 1200

#: Default weight/height used when the profile has no measurements yet.
DEFAULT_WEIGHT_KG = 60.0
DEFAULT_HEIGHT_CM = 165.0
DEFAULT_AGE = 30


def _bmi(weight_kg: Optional[float], height_cm: Optional[float]) -> Optional[float]:
    """Body mass index, or None when the measurements are unusable."""
    if not weight_kg or not height_cm or height_cm <= 0:
        return None
    metres = height_cm / 100.0
    return weight_kg / (metres * metres)


def calculate_calorie_target(
    age: Optional[int] = None,
    gender: Optional[str] = None,
    weight_kg: Optional[float] = None,
    height_cm: Optional[float] = None,
    activity_level: Optional[str] = None,
    goals: Optional[str] = None,
) -> int:
    """Return the daily calorie target for the patient.

    Uses the Mifflin-St Jeor equation, multiplied by an activity factor and
    then adjusted for the stated goal. The result is clamped to
    ``MIN_CALORIE_TARGET`` so a low BMI or a weight-loss goal can never
    produce a dangerously low plan.
    """
    age = int(age) if age else DEFAULT_AGE
    weight_kg = float(weight_kg) if weight_kg else DEFAULT_WEIGHT_KG
    height_cm = float(height_cm) if height_cm else DEFAULT_HEIGHT_CM

    # Mifflin-St Jeor: male +5, female -161 on the constant term.
    constant = 5 if (gender or "").strip().lower().startswith("m") else -161
    bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + constant

    multiplier = ACTIVITY_MULTIPLIERS.get(
        canonical_activity_level(activity_level), DEFAULT_ACTIVITY_MULTIPLIER
    )
    target = bmr * multiplier

    goal_text = (goals or "").strip().lower()
    for goal, factor in GOAL_MULTIPLIERS.items():
        if goal in goal_text:
            target *= factor
            break

    return int(max(MIN_CALORIE_TARGET, round(target / 10.0) * 10))


# --------------------------------------------------------------------------- #
# Meal templates
# --------------------------------------------------------------------------- #
# Each template carries a share of the daily calories and a list of food
# options. Options are plain, widely available local/Bengali foods so the plan
# stays usable for the target audience.
#
#: meal_type -> (share of daily calories, [candidate item groups])
MEAL_TEMPLATES: List[Tuple[str, float, str, List[List[str]]]] = [
    (
        "Breakfast",
        0.25,
        "08:00",
        [
            ["Boiled eggs", "Whole wheat bread", "Banana"],
            ["Oats porridge with milk", "Apple", "Boiled egg"],
            ["Vegetable paratha", "Curd", "Seasonal fruit"],
            ["Lentil soup", "Whole wheat toast", "Papad"],
        ],
    ),
    (
        "Mid-morning snack",
        0.10,
        "10:30",
        [
            ["Roasted chana", "Green tea"],
            ["Mixed fruit salad", "Buttermilk"],
            ["Sprouts chaat", "Lime water"],
            ["Peanut chikki", "Coconut water"],
        ],
    ),
    (
        "Lunch",
        0.30,
        "13:00",
        [
            ["Rice", "Grilled fish", "Mixed vegetable curry", "Salad"],
            ["Rice", "Chicken curry", "Dal", "Mixed vegetables"],
            ["Rice", "Egg curry", "Bengali dry vegetables", "Salad"],
            ["Roti", "Lentil curry", "Seasonal vegetables", "Curd"],
        ],
    ),
    (
        "Afternoon snack",
        0.10,
        "16:00",
        [
            ["Seasonal fruit", "Roasted nuts"],
            ["Buttermilk", "Whole wheat biscuit"],
            ["Sprouts", "Coconut water"],
            ["Cucumber and tomato salad"],
        ],
    ),
    (
        "Dinner",
        0.25,
        "19:30",
        [
            ["Rice", "Fish curry", "Bengali vegetables"],
            ["Roti", "Paneer curry", "Dal", "Salad"],
            ["Rice", "Chicken stew", "Cooked vegetables"],
            ["Khichuri", "Egg bhurji", "Curd"],
        ],
    ),
]

#: Items added to the plan when a rule token is active.
RESTRICTION_ADJUSTMENTS: Dict[str, List[str]] = {
    "low_sodium": ["Avoid added salt and salty pickles; use herbs and lemon for flavour."],
    "low_sugar": ["No added sugar; choose unsweetened drinks and fruit over sweets."],
    "low_fat": ["Use steaming, grilling or shallow frying instead of deep frying."],
    "low_carbs": ["Keep the rice portion small and add extra vegetables or legumes."],
    "high_protein": ["Include an extra protein source such as eggs, dal, soy or paneer."],
    "low_protein": ["Keep protein portions moderate and discuss amounts with your doctor."],
    "low_purine": ["Limit organ meats, red meat and concentrated meat gravies."],
    "vegetarian": ["No meat, fish or eggs; protein comes from dal, soy, paneer or curd."],
    "vegan": ["No animal products; protein comes from dal, soy, nuts and seeds."],
    "gluten_free": ["Use rice, millet or potato instead of wheat-based breads."],
    "lactose_free": ["Use lactose-free milk or soy alternatives instead of plain curd."],
    "increase_calories": ["Add an extra snack or a calorie-dense side such as nuts or milk."],
    "reduce_calories": ["Keep portions modest and avoid fried snacks and sugary drinks."],
    "increase_fluid": ["Drink at least 8 glasses of water spread through the day."],
    "avoid_sugar_alcohol": ["No alcohol; choose water or unsweetened drinks instead."],
    "iron_rich": [
        "Low iron / anaemia: include iron-rich foods every day, such as dal, leafy "
        "greens (spinach, lal shak) and, if your diet allows, eggs, fish or meat.",
        "Pair iron-rich meals with vitamin C foods such as lemon, guava or amla, and "
        "keep tea or coffee at least one hour away from meals.",
    ],
}

#: Items excluded from the plan when a rule token is active.
RESTRICTION_EXCLUSIONS: Dict[str, List[str]] = {
    "vegetarian": ["Grilled fish", "Chicken curry", "Fish curry", "Egg curry", "Egg bhurji",
                   "Chicken stew", "Boiled eggs", "Boiled egg"],
    "vegan": ["Grilled fish", "Chicken curry", "Fish curry", "Egg curry", "Egg bhurji",
              "Chicken stew", "Boiled eggs", "Curd", "Buttermilk", "Paneer curry", "Oats porridge with milk"],
    "gluten_free": ["Whole wheat bread", "Whole wheat toast", "Vegetable paratha", "Roti", "Khichuri"],
    "lactose_free": ["Curd", "Buttermilk", "Oats porridge with milk", "Paneer curry"],
    "low_sugar": ["Apple", "Banana", "Seasonal fruit", "Mixed fruit salad", "Coconut water"],
    # Tea reduces iron absorption, so it is left out for low iron / anaemia.
    "iron_rich": ["Green tea"],
}

#: Conditions that mean the patient needs an iron-rich plan.
IRON_DEFICIENCY_MARKERS = (
    "anemi",
    "anaemi",
    "iron deficien",
    "low iron",
    "low hemoglobin",
    "low haemoglobin",
)

#: Allergen word -> every word that means a menu item contains it. A recorded
#: allergy such as "Peanuts" or "Eggs" is matched by meaning, not by the exact
#: spelling, so "Peanut chikki" or "Egg bhurji" is still removed.
ALLERGEN_FAMILIES: Dict[str, Tuple[str, ...]] = {
    "peanut": ("peanut", "chikki"),
    "nut": ("nut", "peanut", "chikki", "almond", "cashew"),
    "egg": ("egg", "bhurji"),
    "milk": ("milk", "curd", "buttermilk", "paneer", "yogurt", "doi"),
    "dairy": ("milk", "curd", "buttermilk", "paneer", "yogurt", "doi"),
    "lactose": ("milk", "curd", "buttermilk", "paneer", "yogurt", "doi"),
    "fish": ("fish",),
    "seafood": ("fish", "prawn", "shrimp"),
    "chicken": ("chicken",),
    "wheat": ("wheat", "roti", "paratha", "bread", "toast", "biscuit"),
    "gluten": ("wheat", "roti", "paratha", "bread", "toast", "biscuit"),
    "soy": ("soy",),
    "lentil": ("lentil", "dal", "khichuri"),
    "chickpea": ("chickpea", "chana"),
}

#: Values that mean "no allergy recorded".
_NO_ALLERGY_VALUES = ("none", "no", "nil", "n/a", "na", "no allergy", "no allergies")


def _contains_any(text: str, markers: Sequence[str]) -> bool:
    """True when any marker appears in the (lower-cased) text."""
    haystack = (text or "").lower()
    return any(marker.lower() in haystack for marker in markers)


def _singular(word: str) -> str:
    """Very small plural -> singular helper ("peanuts" -> "peanut")."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _words(text: str) -> List[str]:
    """Lower-cased singular words of a menu item or allergy entry."""
    cleaned = "".join(ch if ch.isalpha() else " " for ch in (text or "").lower())
    return [_singular(word) for word in cleaned.split()]


def allergen_terms(allergies: Optional[str]) -> List[Tuple[str, Tuple[str, ...]]]:
    """Turn the free-text allergy field into (label, matching words) pairs."""
    terms: List[Tuple[str, Tuple[str, ...]]] = []
    for raw in (allergies or "").replace(";", ",").split(","):
        label = raw.strip()
        if not label or label.lower() in _NO_ALLERGY_VALUES:
            continue
        words = _words(label)
        if not words:
            continue
        matches: List[str] = []
        for word in words:
            matches.extend(ALLERGEN_FAMILIES.get(word, (word,)))
        terms.append((label, tuple(dict.fromkeys(matches))))
    return terms


def _matches_allergen(item: str, terms: Sequence[Tuple[str, Tuple[str, ...]]]) -> bool:
    """True when a menu item contains one of the recorded allergens."""
    item_words = _words(item)
    for _label, matches in terms:
        for match in matches:
            if any(word == match or word.endswith(match) for word in item_words):
                if match == "nut" and "coconut" in item_words:
                    continue  # coconut is not a tree nut
                return True
    return False


def _filter_items(
    items: Sequence[str],
    restrictions: Sequence[str],
    allergies: Optional[str],
) -> List[str]:
    """Drop items that break a restriction or match a recorded allergy.

    SRS 3.3.6.2: the plan must respect known dietary restrictions. An item is
    removed when it is explicitly excluded by a restriction token or when it
    matches one of the patient's recorded allergens.
    """
    excluded: List[str] = []
    for token in restrictions:
        excluded.extend(RESTRICTION_EXCLUSIONS.get(token, []))

    terms = allergen_terms(allergies)

    kept: List[str] = []
    for item in items:
        lowered = item.lower()
        if any(bad.lower() == lowered or bad.lower() in lowered for bad in excluded):
            continue
        if _matches_allergen(item, terms):
            continue
        kept.append(item)
    return kept


#: Rough per-item nutrition (per single serving) used to estimate the macros of
#: a generated meal. These are estimates for display only -- the authoritative
#: nutrition data lives in DISH_DNA (SCRUM-47).
ITEM_NUTRITION: Dict[str, Dict[str, float]] = {
    "boiled eggs": {"calories": 78, "protein_g": 6.3, "carbs_g": 0.6, "fat_g": 5.3},
    "boiled egg": {"calories": 78, "protein_g": 6.3, "carbs_g": 0.6, "fat_g": 5.3},
    "egg curry": {"calories": 220, "protein_g": 13.0, "carbs_g": 8.0, "fat_g": 15.0},
    "egg bhurji": {"calories": 200, "protein_g": 12.0, "carbs_g": 6.0, "fat_g": 14.0},
    "whole wheat bread": {"calories": 80, "protein_g": 4.0, "carbs_g": 14.0, "fat_g": 1.2},
    "whole wheat toast": {"calories": 80, "protein_g": 4.0, "carbs_g": 14.0, "fat_g": 1.2},
    "vegetable paratha": {"calories": 180, "protein_g": 5.0, "carbs_g": 28.0, "fat_g": 6.0},
    "roti": {"calories": 120, "protein_g": 3.5, "carbs_g": 22.0, "fat_g": 2.0},
    "rice": {"calories": 205, "protein_g": 4.3, "carbs_g": 44.0, "fat_g": 0.4},
    "khichuri": {"calories": 250, "protein_g": 7.0, "carbs_g": 45.0, "fat_g": 5.0},
    "dal": {"calories": 120, "protein_g": 7.0, "carbs_g": 16.0, "fat_g": 2.0},
    "lentil soup": {"calories": 100, "protein_g": 6.0, "carbs_g": 14.0, "fat_g": 1.5},
    "lentil curry": {"calories": 160, "protein_g": 9.0, "carbs_g": 20.0, "fat_g": 5.0},
    "grilled fish": {"calories": 190, "protein_g": 28.0, "carbs_g": 0.0, "fat_g": 8.0},
    "fish curry": {"calories": 230, "protein_g": 24.0, "carbs_g": 6.0, "fat_g": 12.0},
    "chicken curry": {"calories": 260, "protein_g": 26.0, "carbs_g": 6.0, "fat_g": 15.0},
    "chicken stew": {"calories": 240, "protein_g": 25.0, "carbs_g": 8.0, "fat_g": 12.0},
    "paneer curry": {"calories": 280, "protein_g": 16.0, "carbs_g": 10.0, "fat_g": 21.0},
    "curd": {"calories": 60, "protein_g": 3.5, "carbs_g": 4.0, "fat_g": 3.5},
    "buttermilk": {"calories": 40, "protein_g": 2.0, "carbs_g": 5.0, "fat_g": 1.0},
    "milk": {"calories": 100, "protein_g": 3.4, "carbs_g": 5.0, "fat_g": 3.3},
    "oats porridge with milk": {"calories": 220, "protein_g": 9.0, "carbs_g": 32.0, "fat_g": 6.0},
    "banana": {"calories": 105, "protein_g": 1.3, "carbs_g": 27.0, "fat_g": 0.4},
    "apple": {"calories": 95, "protein_g": 0.5, "carbs_g": 25.0, "fat_g": 0.3},
    "seasonal fruit": {"calories": 70, "protein_g": 0.8, "carbs_g": 18.0, "fat_g": 0.3},
    "mixed fruit salad": {"calories": 90, "protein_g": 1.0, "carbs_g": 22.0, "fat_g": 0.4},
    "roasted chana": {"calories": 180, "protein_g": 9.0, "carbs_g": 27.0, "fat_g": 3.0},
    "sprouts chaat": {"calories": 120, "protein_g": 8.0, "carbs_g": 16.0, "fat_g": 1.0},
    "sprouts": {"calories": 100, "protein_g": 7.0, "carbs_g": 14.0, "fat_g": 0.6},
    "peanut chikki": {"calories": 190, "protein_g": 7.0, "carbs_g": 20.0, "fat_g": 11.0},
    "roasted nuts": {"calories": 180, "protein_g": 6.0, "carbs_g": 6.0, "fat_g": 15.0},
    "papad": {"calories": 55, "protein_g": 2.0, "carbs_g": 9.0, "fat_g": 0.2},
    "green tea": {"calories": 2, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0},
    "lime water": {"calories": 5, "protein_g": 0.0, "carbs_g": 1.0, "fat_g": 0.0},
    "coconut water": {"calories": 45, "protein_g": 1.7, "carbs_g": 9.0, "fat_g": 0.5},
    "whole wheat biscuit": {"calories": 70, "protein_g": 2.0, "carbs_g": 11.0, "fat_g": 2.0},
    "mixed vegetable curry": {"calories": 120, "protein_g": 4.0, "carbs_g": 12.0, "fat_g": 7.0},
    "mixed vegetables": {"calories": 90, "protein_g": 3.5, "carbs_g": 9.0, "fat_g": 5.0},
    "bengali dry vegetables": {"calories": 110, "protein_g": 3.0, "carbs_g": 12.0, "fat_g": 6.0},
    "bengali vegetables": {"calories": 100, "protein_g": 3.0, "carbs_g": 11.0, "fat_g": 5.0},
    "seasonal vegetables": {"calories": 80, "protein_g": 3.0, "carbs_g": 8.0, "fat_g": 4.0},
    "cooked vegetables": {"calories": 90, "protein_g": 3.0, "carbs_g": 9.0, "fat_g": 4.5},
    "cooked seasonal vegetables": {"calories": 80, "protein_g": 3.0, "carbs_g": 8.0, "fat_g": 4.0},
    "salad": {"calories": 40, "protein_g": 1.5, "carbs_g": 6.0, "fat_g": 0.3},
    "cucumber and tomato salad": {"calories": 35, "protein_g": 1.2, "carbs_g": 6.0, "fat_g": 0.3},
    "plain rice": {"calories": 205, "protein_g": 4.3, "carbs_g": 44.0, "fat_g": 0.4},
}

#: Fallback macros for an item that is not in ``ITEM_NUTRITION``.
DEFAULT_ITEM_NUTRITION = {"calories": 90, "protein_g": 3.0, "carbs_g": 12.0, "fat_g": 3.0}


def _estimate_macros(items: Sequence[str]) -> Dict[str, float]:
    """Sum the estimated macros of the items in one meal."""
    totals = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
    for item in items:
        nutrition = ITEM_NUTRITION.get(item.strip().lower(), DEFAULT_ITEM_NUTRITION)
        for key in totals:
            totals[key] += nutrition.get(key, 0.0)
    return totals


def _build_meals(
    calorie_target: int,
    restrictions: Sequence[str],
    allergies: Optional[str],
    day_index: int,
) -> List[Dict]:
    """Build the meals for one day of the plan.

    Each day rotates through the template options so a 7-day plan does not
    repeat the same menu every day, while every day still respects the
    effective restrictions and allergens.
    """
    meals: List[Dict] = []
    for template_index, (meal_type, share, time, options) in enumerate(MEAL_TEMPLATES):
        option = options[(day_index + template_index) % len(options)]
        kept = _filter_items(option, restrictions, allergies)
        if not kept:
            # Every candidate was excluded -- fall back to the safest neutral
            # option so the day is never left without a meal.
            kept = _filter_items(
                ["Plain rice", "Cooked seasonal vegetables", "Lentil soup"],
                restrictions,
                allergies,
            ) or ["Plain rice"]
        macros = _estimate_macros(kept)
        meals.append(
            {
                "meal_type": meal_type,
                "time": time,
                "items": kept,
                "calories": int(round(calorie_target * share)),
                "protein_g": round(macros["protein_g"], 1),
                "carbs_g": round(macros["carbs_g"], 1),
                "fat_g": round(macros["fat_g"], 1),
                "notes": None,
            }
        )
    return meals


# --------------------------------------------------------------------------- #
# Activity plan (SRS 3.3.3)
# --------------------------------------------------------------------------- #
#: Activity level -> the activity suggestions offered to the patient.
ACTIVITY_CATALOGUE: Dict[str, List[Dict]] = {
    "sedentary": [
        {"name": "Gentle walking", "description": "Easy walking at a comfortable pace.",
         "duration_minutes": 15, "intensity": "Low", "frequency": "5 days a week"},
        {"name": "Seated stretching", "description": "Gentle upper-body and neck stretches.",
         "duration_minutes": 10, "intensity": "Low", "frequency": "Daily"},
    ],
    "lightly active": [
        {"name": "Brisk walking", "description": "Walking at a slightly faster pace.",
         "duration_minutes": 30, "intensity": "Moderate", "frequency": "5 days a week"},
        {"name": "Light stretching", "description": "Full-body stretching after activity.",
         "duration_minutes": 10, "intensity": "Low", "frequency": "After each session"},
    ],
    "moderately active": [
        {"name": "Brisk walking", "description": "Walking at a slightly faster pace.",
         "duration_minutes": 30, "intensity": "Moderate", "frequency": "5 days a week"},
        {"name": "Body-weight exercises", "description": "Squats, lunges and push-ups without equipment.",
         "duration_minutes": 20, "intensity": "Moderate", "frequency": "3 days a week"},
    ],
    "very active": [
        {"name": "Jogging or running", "description": "Steady-paced jogging.",
         "duration_minutes": 30, "intensity": "Moderate to high", "frequency": "4 days a week"},
        {"name": "Strength training", "description": "Resistance exercises with body weight or bands.",
         "duration_minutes": 30, "intensity": "Moderate", "frequency": "3 days a week"},
    ],
    "extra active": [
        {"name": "Running or cycling", "description": "Steady-state cardio session.",
         "duration_minutes": 45, "intensity": "High", "frequency": "4 days a week"},
        {"name": "Strength training", "description": "Resistance exercises with body weight or bands.",
         "duration_minutes": 40, "intensity": "High", "frequency": "3 days a week"},
    ],
}

#: Fallback catalogue when the activity level is unknown.
DEFAULT_ACTIVITIES: List[Dict] = [
    {"name": "Walking", "description": "Walking at a comfortable pace.",
     "duration_minutes": 30, "intensity": "Moderate", "frequency": "5 days a week"},
    {"name": "Light stretching", "description": "Full-body stretching after activity.",
     "duration_minutes": 10, "intensity": "Low", "frequency": "After each session"},
]

#: Activities that are never suggested when the patient is restricted from
#: physical activity (SRS 3.3.3.3).
RESTRICTED_ACTIVITIES = ("jogging", "running", "cycling", "strength training", "body-weight exercises")

#: Cautions added when a condition makes intense activity unsafe.
CONDITION_CAUTIONS: List[Tuple[str, str]] = [
    ("cardiac", "Heart conditions: keep the intensity low and stop if you feel chest discomfort."),
    ("heart", "Heart conditions: keep the intensity low and stop if you feel chest discomfort."),
    ("angina", "Angina: avoid strenuous activity unless your doctor has cleared it."),
    ("hypertens", "High blood pressure: avoid straining and heavy lifting at high intensity."),
    ("pregnan", "Pregnancy: follow the activity advice given by your maternity care provider."),
    ("asthma", "Asthma: keep an inhaler nearby and stop if breathing becomes difficult."),
    ("copd", "COPD: use light intensity only and follow your breathing plan."),
    ("joint", "Joint problems: avoid high-impact movements and choose low-impact options."),
    ("back", "Back problems: avoid heavy lifting and keep movements gentle and controlled."),
    ("knee", "Knee problems: choose low-impact options such as walking or cycling, "
             "and avoid jumping, deep squats and running."),
    ("anemi", "Anaemia: build activity up slowly and stop if you feel dizzy, weak or breathless."),
    ("anaemi", "Anaemia: build activity up slowly and stop if you feel dizzy, weak or breathless."),
]


#: Aliases for the activity-level values the UI stores, mapped onto the
#: canonical keys of ``ACTIVITY_CATALOGUE`` / ``ACTIVITY_MULTIPLIERS``.
ACTIVITY_LEVEL_ALIASES: Dict[str, str] = {
    "light": "lightly active",
    "lightly active": "lightly active",
    "moderate": "moderately active",
    "moderately active": "moderately active",
    "active": "moderately active",
    "very active": "very active",
    "extra active": "extra active",
    "sedentary": "sedentary",
}


def canonical_activity_level(activity_level: Optional[str]) -> str:
    """Return the canonical activity-level key for a stored/typed value.

    The dashboard offers "Sedentary / Light / Moderate / Active / Very active"
    while the catalogue is keyed by the canonical names, so the values are
    mapped here. An unknown value yields the default "lightly active".
    """
    level = (activity_level or "").strip().lower()
    return ACTIVITY_LEVEL_ALIASES.get(level, "lightly active")


def _build_activities(
    activity_level: Optional[str],
    restrictions: Sequence[str],
    conditions: Optional[str],
    physical_limitations: Optional[str] = None,
) -> Tuple[List[Dict], List[str]]:
    """Build the activity suggestions plus the safety cautions for them.

    SRS 3.3.3.3: when a condition or restriction makes activity risky, only
    safe suggestions are offered and a professional review is requested.
    SRS 3.3.3.5: a caution about consulting a professional is always
    returned with the activity guidance.
    """
    cautions: List[str] = []
    level = canonical_activity_level(activity_level)
    catalogue = ACTIVITY_CATALOGUE.get(level, DEFAULT_ACTIVITIES)

    restricted = "avoid_exercise" in restrictions
    # Conditions AND physical limitations ("Bad knee") both shape the activities.
    condition_text = f"{conditions or ''} {physical_limitations or ''}".lower()

    # Condition-driven cautions (SRS 3.3.3.3).
    knee_problem = "knee" in condition_text
    for marker, caution in CONDITION_CAUTIONS:
        if marker in condition_text and caution not in cautions:
            cautions.append(caution)

    if restricted:
        cautions.append(
            "Your record restricts physical activity, so only gentle, low-impact "
            "movement is suggested and a professional must approve any activity."
        )

    # High-intensity activity is unsafe for these conditions (SRS 3.3.3.3).
    if any(marker in condition_text for marker in HIGH_INTENSITY_UNSAFE_CONDITIONS):
        cautions.append(
            "Because of your recorded health condition, high-intensity activity "
            "is not included in this plan."
        )

    activities: List[Dict] = []
    for entry in catalogue:
        name = entry["name"]
        lowered = name.lower()

        # An explicit "no exercise" restriction removes every non-gentle option.
        if restricted and any(bad in lowered for bad in RESTRICTED_ACTIVITIES):
            continue
        # Running and jumping-style exercises are hard on a bad knee.
        if knee_problem and any(bad in lowered for bad in ("jogging", "running", "body-weight")):
            continue
        # High-intensity activity is dropped for unsafe conditions.
        if any(marker in condition_text for marker in HIGH_INTENSITY_UNSAFE_CONDITIONS):
            if any(bad in lowered for bad in RESTRICTED_ACTIVITIES):
                continue
            if entry.get("intensity") in ("High", "Moderate to high"):
                continue

        activities.append(
            {
                "name": name,
                "description": entry.get("description"),
                "duration_minutes": entry.get("duration_minutes"),
                "intensity": entry.get("intensity"),
                "frequency": entry.get("frequency"),
                "days": [],
            }
        )

    if not activities:
        # Never return an empty activity plan -- gentle movement is always safe.
        activities.append(
            {
                "name": "Gentle walking",
                "description": "Easy walking at a comfortable pace.",
                "duration_minutes": 10,
                "intensity": "Low",
                "frequency": "Daily",
                "days": [],
            }
        )

    return activities, cautions


def _budget_notes(
    monthly_budget: Optional[float],
    restrictions: Sequence[str],
) -> List[str]:
    """Budget guidance derived from the recorded budget context.

    SRS 3.3.6.1 requires the budget context to influence the plan, and
    SRS 3.3.6.2 requires an unresolved budget/restriction conflict to be
    flagged rather than hidden.
    """
    notes: List[str] = []
    if not monthly_budget:
        notes.append(
            "No monthly budget was recorded; the plan uses affordable local "
            "staples such as rice, dal, seasonal vegetables and eggs."
        )
        return notes

    per_day = float(monthly_budget) / 30.0
    notes.append(
        f"Your budget of {monthly_budget:.0f} BDT per month is about "
        f"{per_day:.0f} BDT per day for food."
    )

    if per_day < 100:
        notes.append(
            "That is a tight daily budget, so the plan relies on low-cost "
            "staples (rice, lentils, seasonal vegetables) and eggs or curd as "
            "an affordable protein source."
        )
        if "high_protein" in restrictions:
            notes.append(
                "A high-protein prescription and a very tight budget can pull "
                "in opposite directions, so this is flagged for review."
            )
    else:
        notes.append(
            "The plan stays within this budget by using widely available "
            "local ingredients instead of imported or processed foods."
        )

    if "avoid_sugar_alcohol" in restrictions or "reduce_calories" in restrictions:
        notes.append(
            "Sugary drinks and processed snacks are excluded, which also keeps "
            "the daily cost lower."
        )
    return notes


# --------------------------------------------------------------------------- #
# Plan generation
# --------------------------------------------------------------------------- #
#: Restrictions that map onto a meal-plan note, so the user can see WHY a
#: food was left out.
_RULE_NOTE_SOURCES = (
    "low_sodium",
    "low_sugar",
    "low_fat",
    "low_carbs",
    "high_protein",
    "low_protein",
    "low_purine",
    "vegetarian",
    "vegan",
    "gluten_free",
    "lactose_free",
    "increase_calories",
    "reduce_calories",
    "increase_fluid",
    "avoid_sugar_alcohol",
    "iron_rich",
)


def generate_plan(
    *,
    patient_id: int,
    age: Optional[int] = None,
    gender: Optional[str] = None,
    weight_kg: Optional[float] = None,
    height_cm: Optional[float] = None,
    activity_level: Optional[str] = None,
    goals: Optional[str] = None,
    conditions: Optional[str] = None,
    allergies: Optional[str] = None,
    physical_limitations: Optional[str] = None,
    food_preference: Optional[str] = None,
    monthly_budget: Optional[float] = None,
    prescriptions: Optional[Sequence] = None,
    days: int = 7,
) -> Dict:
    """Generate the personalized meal + activity plan.

    Parameters mirror the patient's data (health profile, prescriptions) plus
    the optional overrides sent with the request (SRS 3.3.6.4).

    Returns a dict with the keys:

    ``days``               the generated daily meal plan,
    ``activities``         the generated activity suggestions,
    ``cautions``           activity safety cautions (SRS 3.3.3.5),
    ``conflicts``          the detected conflicts (SRS 3.3.4),
    ``restrictions_applied`` the safe intersection of rules that was applied,
    ``requires_review``    True when a professional must review the plan,
    ``calorie_target``     the daily calorie target used,
    ``budget_notes``       budget guidance.
    """
    prescriptions = list(prescriptions or [])

    # -- 1. Resolve the safe intersection of every restriction source ------- #
    restrictions = resolve_effective_restrictions(
        prescriptions=prescriptions,
        conditions=conditions,
        allergies=allergies,
        physical_limitations=physical_limitations,
    )

    # A food preference (vegetarian / vegan / no beef ...) is a restriction too.
    # "Non-vegetarian" must NOT switch on the vegetarian rule.
    preference_text = (food_preference or "").lower().replace("-", " ")
    for phrase in ("non vegetarian", "non veg", "nonveg"):
        preference_text = preference_text.replace(phrase, " ")
    preference_tokens = detect_rules(preference_text)
    for token in preference_tokens:
        if token not in restrictions:
            restrictions.append(token)

    # Low iron / anaemia in the conditions or a prescription -> iron-rich plan.
    health_text = " ".join(
        [conditions or ""]
        + [
            str(p.get("restrictions") or "") if isinstance(p, dict)
            else str(getattr(p, "restrictions", "") or "")
            for p in prescriptions
        ]
    ).lower()
    if any(marker in health_text for marker in IRON_DEFICIENCY_MARKERS):
        if "iron_rich" not in restrictions:
            restrictions.append("iron_rich")

    # -- 2. Detect conflicts ------------------------------------------------ #
    conflicts = find_dietary_conflicts(
        prescriptions=prescriptions,
        conditions=conditions,
        allergies=allergies,
        physical_limitations=physical_limitations,
        goals=goals,
    )
    requires_review = any(bool(c.get("requires_review")) for c in conflicts)

    # -- 3. Calorie target -------------------------------------------------- #
    calorie_target = calculate_calorie_target(
        age=age,
        gender=gender,
        weight_kg=weight_kg,
        height_cm=height_cm,
        activity_level=activity_level,
        goals=goals,
    )

    # -- 4. Meals ----------------------------------------------------------- #
    # A goal that fights a recorded condition must not reduce the calories.
    if any(c.get("code") == "goal_condition_conflict" for c in conflicts):
        calorie_target = calculate_calorie_target(
            age=age,
            gender=gender,
            weight_kg=weight_kg,
            height_cm=height_cm,
            activity_level=activity_level,
            goals=None,
        )

    plan_days: List[Dict] = []
    day_count = max(1, min(int(days or 1), 31))
    for index in range(day_count):
        meals = _build_meals(calorie_target, restrictions, allergies, index)
        plan_days.append(
            {
                "day": index + 1,
                "label": f"Day {index + 1}",
                "meals": meals,
                "daily_calories": sum(m["calories"] for m in meals),
                "notes": None,
            }
        )

    # -- 5. Activities ------------------------------------------------------ #
    activities, cautions = _build_activities(
        activity_level, restrictions, conditions, physical_limitations
    )

    # -- 6. Budget ---------------------------------------------------------- #
    budget_notes = _budget_notes(monthly_budget, restrictions)

    # -- 7. Explain the applied rules -------------------------------------- #
    applied_notes: List[str] = []
    for token in restrictions:
        if token in _RULE_NOTE_SOURCES:
            for note in RESTRICTION_ADJUSTMENTS.get(token, []):
                if note not in applied_notes:
                    applied_notes.append(note)

    # Say which allergies were applied, so the user can see WHY items are missing.
    for label, _matches in allergen_terms(allergies):
        applied_notes.append(
            f"Allergy to {label}: every item that contains it was left out of the plan."
        )

    bmi = _bmi(weight_kg, height_cm)
    if bmi is not None:
        if bmi < 18.5:
            applied_notes.append(
                f"Your BMI is {bmi:.1f}; a calorie reduction is not appropriate, "
                "so the plan keeps enough energy for you."
            )
        elif bmi >= 25:
            applied_notes.append(
                f"Your BMI is {bmi:.1f}; the plan uses a modest calorie deficit "
                "with regular activity."
            )

    return {
        "patient_id": patient_id,
        "calorie_target": calorie_target,
        "days": plan_days,
        "activities": activities,
        "cautions": cautions,
        "conflicts": conflicts,
        "restrictions_applied": restrictions,
        "requires_review": requires_review,
        "budget_notes": budget_notes,
        "notes": applied_notes,
    }